#include "WebRtcClient.h"

#include <juce_core/juce_core.h>
#include <rtc/rtc.hpp>
#include <opus/opus.h>

#include <chrono>
#include <vector>

using namespace std::chrono_literals;

// libdatachannel + Opus live behind this pImpl so the JUCE translation units never see them.
struct WebRtcClient::Impl
{
    std::shared_ptr<rtc::PeerConnection> pc;
    std::shared_ptr<rtc::Track> track;
    std::shared_ptr<rtc::DataChannel> stats;
    OpusEncoder* enc = nullptr;
    OpusDecoder* dec = nullptr;
    ~Impl()
    {
        if (enc) opus_encoder_destroy(enc);
        if (dec) opus_decoder_destroy(dec);
    }
};

WebRtcClient::WebRtcClient (StereoFifo& toNetwork, StereoFifo& fromNetwork)
    : toNet (toNetwork), fromNet (fromNetwork), impl (std::make_unique<Impl>()) {}

WebRtcClient::~WebRtcClient() { disconnect(); }

void WebRtcClient::connect (const std::string& baseUrl, Mode mode, const std::string& channel)
{
    if (worker.joinable()) return;
    stopFlag = false;
    worker = std::thread ([this, baseUrl, mode, channel] { run (baseUrl, mode, channel); });
}

void WebRtcClient::disconnect()
{
    stopFlag = true;
    if (worker.joinable()) worker.join();
    connected = false;
}

// --- signalling over plain HTTP (juce::URL → WinINet on Windows, no curl needed) ---------------
static rtc::Configuration fetchIceConfig (const std::string& baseUrl)
{
    rtc::Configuration config;
    auto stream = juce::URL (juce::String (baseUrl) + "/ice").createInputStream (
        juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inAddress).withConnectionTimeoutMs (15000));
    if (stream == nullptr) return config;

    auto json = juce::JSON::parse (stream->readEntireStreamAsString());
    if (auto* servers = json.getProperty ("iceServers", {}).getArray())
    {
        for (auto& s : *servers)
        {
            const auto user = s.getProperty ("username", {}).toString().toStdString();
            const auto cred = s.getProperty ("credential", {}).toString().toStdString();
            if (auto* urls = s.getProperty ("urls", {}).getArray())
            {
                for (auto& u : *urls)
                {
                    const juce::String url = u.toString();
                    if (url.startsWith ("stun:"))
                    {
                        config.iceServers.emplace_back (url.toStdString());
                    }
                    else if (url.startsWith ("turn:") || url.startsWith ("turns:"))
                    {
                        // turn[s]:HOST:PORT?transport=...
                        const bool tls = url.startsWith ("turns:");
                        auto rest = url.fromFirstOccurrenceOf (":", false, false).upToFirstOccurrenceOf ("?", false, false);
                        const auto host = rest.upToLastOccurrenceOf (":", false, false).toStdString();
                        const auto port = (uint16_t) rest.fromLastOccurrenceOf (":", false, false).getIntValue();
                        config.iceServers.emplace_back (host, port, user, cred,
                            tls ? rtc::IceServer::RelayType::TurnTls : rtc::IceServer::RelayType::TurnUdp);
                    }
                }
            }
        }
    }
    return config;
}

static std::string postOffer (const std::string& baseUrl, const std::string& path,
                              const std::string& offerSdp, const std::string& channel)
{
    auto* obj = new juce::DynamicObject();
    obj->setProperty ("type", "offer");
    obj->setProperty ("sdp", juce::String (offerSdp));
    if (! channel.empty()) obj->setProperty ("channel", juce::String (channel));
    const juce::String body = juce::JSON::toString (juce::var (obj));

    auto url = juce::URL (juce::String (baseUrl) + path).withPOSTData (body);
    auto stream = url.createInputStream (
        juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inPostData)
            .withExtraHeaders ("Content-Type: application/json")
            .withConnectionTimeoutMs (60000));
    if (stream == nullptr) return {};

    auto json = juce::JSON::parse (stream->readEntireStreamAsString());
    return json.getProperty ("sdp", {}).toString().toStdString();
}

void WebRtcClient::run (std::string baseUrl, Mode mode, std::string channel)
{
    try
    {
        const bool recv = (mode == Mode::Receive);  // downlink only: no mic uplink, POST /subscribe
        auto config = fetchIceConfig (baseUrl);
        impl->pc = std::make_shared<rtc::PeerConnection> (config);
        impl->pc->onStateChange ([this] (rtc::PeerConnection::State s)
        {
            connected = (s == rtc::PeerConnection::State::Connected);
        });

        int err = 0;
        impl->enc = opus_encoder_create (48000, 2, OPUS_APPLICATION_AUDIO, &err);
        impl->dec = opus_decoder_create (48000, 2, &err);

        const uint32_t ssrc = 42;
        rtc::Description::Audio media ("audio",
            recv ? rtc::Description::Direction::RecvOnly : rtc::Description::Direction::SendRecv);
        media.addOpusCodec (111);
        if (! recv) media.addSSRC (ssrc, "relaysplit");  // only a broadcaster sends
        impl->track = impl->pc->addTrack (media);

        if (! recv)
        {
            auto rtpConfig = std::make_shared<rtc::RtpPacketizationConfig> (
                ssrc, "relaysplit", 111, rtc::OpusRtpPacketizer::DefaultClockRate);
            impl->track->setMediaHandler (std::make_shared<rtc::OpusRtpPacketizer> (rtpConfig));
        }

        // Receive: parse incoming RTP ourselves (the depacketizer template isn't exported from the
        // vcpkg DLL), strip the RTP header, Opus-decode the payload → from-network FIFO.
        impl->track->onMessage ([this] (rtc::message_variant msg)
        {
            if (! std::holds_alternative<rtc::binary> (msg)) return;
            const auto& pkt = std::get<rtc::binary> (msg);
            if (pkt.size() < 12) return;
            const auto* b = reinterpret_cast<const uint8_t*> (pkt.data());
            if ((b[1] & 0x7F) != 111) return;            // only our Opus payload type (skip RTCP/etc.)
            size_t off = 12 + (size_t) (b[0] & 0x0F) * 4;  // fixed header + CSRCs
            if ((b[0] & 0x10) && pkt.size() >= off + 4)    // optional header extension
                off += 4 + (size_t) ((b[off + 2] << 8) | b[off + 3]) * 4;
            if (pkt.size() <= off) return;
            float pcm[5760 * 2];                           // up to 120 ms @ 48 kHz stereo
            const int n = opus_decode_float (impl->dec, b + off, (opus_int32) (pkt.size() - off), pcm, 5760, 0);
            if (n > 0) fromNet.push (pcm, n);
        });

        // Stats channel: the container reports per-chunk inference time as {"infer_ms": N}.
        impl->stats = impl->pc->createDataChannel ("stats");
        impl->stats->onMessage ([this] (rtc::message_variant msg)
        {
            if (std::holds_alternative<rtc::string> (msg))
            {
                auto v = juce::JSON::parse (juce::String (std::get<rtc::string> (msg)));
                if (v.hasProperty ("infer_ms"))
                    inferenceMsAtomic = (float) (double) v.getProperty ("infer_ms", 0.0);
            }
        });

        // Non-trickle offer: gather fully, then POST to /offer (the spike's TURN-permission lesson).
        impl->pc->setLocalDescription();
        for (int i = 0; i < 100 && ! stopFlag; ++i)
        {
            if (impl->pc->gatheringState() == rtc::PeerConnection::GatheringState::Complete) break;
            std::this_thread::sleep_for (50ms);
        }
        std::string offerSdp;
        if (auto local = impl->pc->localDescription()) offerSdp = std::string (local->generateSdp());
        const auto answerSdp = postOffer (baseUrl, recv ? "/subscribe" : "/offer", offerSdp, channel);
        if (! answerSdp.empty())
            impl->pc->setRemoteDescription (rtc::Description (answerSdp, "answer"));

        if (recv)
        {
            // Receiver: pure downlink — the separated audio arrives via track->onMessage above and is
            // pushed to the from-network FIFO. Nothing to send; just keep the RTT meter fresh.
            while (! stopFlag)
            {
                if (auto r = impl->pc->rtt()) rttMsAtomic = (float) r->count();
                std::this_thread::sleep_for (200ms);
            }
        }
        else
        {
            // Broadcaster send loop: 20 ms (960-frame) Opus packets from the to-network FIFO.
            uint32_t ts = 0;
            std::vector<float> in ((size_t) 960 * 2);
            std::vector<unsigned char> packet (4000);
            while (! stopFlag)
            {
                if (toNet.numReady() >= 960 && impl->track && impl->track->isOpen())
                {
                    toNet.pop (in.data(), 960);
                    const int bytes = opus_encode_float (impl->enc, in.data(), 960, packet.data(), (opus_int32) packet.size());
                    if (bytes > 0)
                    {
                        auto* p = reinterpret_cast<std::byte*> (packet.data());
                        impl->track->sendFrame (rtc::binary (p, p + bytes), rtc::FrameInfo (ts));
                        ts += 960;
                    }
                }
                else
                {
                    std::this_thread::sleep_for (2ms);
                }
                if (auto r = impl->pc->rtt()) rttMsAtomic = (float) r->count();
            }
        }

        if (impl->pc) impl->pc->close();
    }
    catch (const std::exception& e)
    {
        juce::Logger::writeToLog (juce::String ("WebRtcClient error: ") + e.what());
        connected = false;
    }
}
