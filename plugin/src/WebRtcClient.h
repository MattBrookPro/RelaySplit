#pragma once
#include <atomic>
#include <memory>
#include <string>
#include <thread>
#include "StereoFifo.h"

// Native WebRTC client to the Modal separator. Owns a libdatachannel PeerConnection + Opus codec on
// a WORKER THREAD; the audio thread only ever touches the two FIFOs. Signalling replicates the
// browser exactly: GET <base>/ice for the ICE servers, POST <base>/offer with the local SDP, apply
// the answer. libdatachannel/Opus types are hidden behind a pImpl so the JUCE TUs don't include them.
//
// Audio is 48 kHz stereo on the wire (Opus). For the first cut the host is assumed to run at 48 kHz;
// resampling arbitrary host rates is a documented follow-up.
class WebRtcClient
{
public:
    // Broadcast: send this track's input up + monitor the separated stream back (POST /offer).
    // Receive:   downlink only — tune into a peer's broadcast, no uplink audio (POST /subscribe).
    enum class Mode { Broadcast, Receive };

    WebRtcClient (StereoFifo& toNetwork, StereoFifo& fromNetwork);
    ~WebRtcClient();

    // non-blocking: spins the worker + signalling. `channel` keys the broadcast (empty = private solo).
    void connect (const std::string& baseUrl, Mode mode = Mode::Broadcast, const std::string& channel = {});
    void disconnect();

    bool  isConnected() const  { return connected.load(); }
    float rttMs() const        { return rttMsAtomic.load(); }
    float inferenceMs() const  { return inferenceMsAtomic.load(); }

private:
    void run (std::string baseUrl, Mode mode, std::string channel);  // worker-thread body

    StereoFifo& toNet;
    StereoFifo& fromNet;
    std::thread worker;
    std::atomic<bool>  connected { false };
    std::atomic<bool>  stopFlag  { false };
    std::atomic<float> rttMsAtomic { 0.0f };
    std::atomic<float> inferenceMsAtomic { 0.0f };

    struct Impl;
    std::unique_ptr<Impl> impl;  // libdatachannel PeerConnection/Track, Opus enc/dec
};
