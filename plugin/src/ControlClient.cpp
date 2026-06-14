#include "ControlClient.h"

ControlClient& ControlClient::get() { static ControlClient instance; return instance; }

juce::var ControlClient::get (const juce::String& path)
{
    juce::URL url (juce::String (kBase) + path);
    auto opts = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inAddress)
                    .withExtraHeaders (token.isNotEmpty() ? ("Authorization: Bearer " + token) : juce::String())
                    .withConnectionTimeoutMs (15000);
    auto stream = url.createInputStream (opts);
    return stream ? juce::JSON::parse (stream->readEntireStreamAsString()) : juce::var();
}

juce::var ControlClient::post (const juce::String& path, const juce::var& body)
{
    auto url = juce::URL (juce::String (kBase) + path).withPOSTData (juce::JSON::toString (body));
    juce::String headers = "Content-Type: application/json";
    if (token.isNotEmpty()) headers << "\r\nAuthorization: Bearer " << token;
    auto opts = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inPostData)
                    .withExtraHeaders (headers)
                    .withConnectionTimeoutMs (20000);
    auto stream = url.createInputStream (opts);
    return stream ? juce::JSON::parse (stream->readEntireStreamAsString()) : juce::var();
}

juce::String ControlClient::login (const juce::String& user, const juce::String& pass)
{
    auto* o = new juce::DynamicObject();
    o->setProperty ("username", user);
    o->setProperty ("password", pass);
    auto res = post ("/api/login", juce::var (o));
    if (! res.isObject()) return "no response from server";
    if (res.hasProperty ("error")) return res["error"].toString();
    token = res["token"].toString();
    username = user;
    cachedPeers.clear();
    peers (true);
    return token.isNotEmpty() ? juce::String() : "login failed";
}

void ControlClient::logout() { token = {}; username = {}; cachedPeers.clear(); }

const std::vector<Peer>& ControlClient::peers (bool forceRefresh)
{
    if (! forceRefresh && ! cachedPeers.empty()) return cachedPeers;
    cachedPeers.clear();
    auto res = get ("/api/peers");
    if (auto* arr = res.getProperty ("peers", {}).getArray())
        for (auto& p : *arr)
            cachedPeers.push_back ({ (int) p["id"], p["username"].toString() });
    return cachedPeers;
}

int ControlClient::createChannel (const juce::String& name, const juce::String& stem)
{
    auto* o = new juce::DynamicObject();
    o->setProperty ("name", name);
    o->setProperty ("stem", stem);
    auto res = post ("/api/channels", juce::var (o));
    auto ch = res.getProperty ("channel", {});
    return ch.isObject() ? (int) ch["id"] : 0;
}

std::set<int> ControlClient::getShares (int channelId)
{
    std::set<int> out;
    auto res = get ("/api/channels/" + juce::String (channelId) + "/shares");
    if (auto* arr = res.getProperty ("shared", {}).getArray())
        for (auto& s : *arr) out.insert ((int) s["id"]);
    return out;
}

bool ControlClient::setShares (int channelId, const std::set<int>& peerIds)
{
    juce::Array<juce::var> ids;
    for (int id : peerIds) ids.add (id);
    auto* o = new juce::DynamicObject();
    o->setProperty ("peerIds", juce::var (ids));
    auto res = post ("/api/channels/" + juce::String (channelId) + "/shares", juce::var (o));
    return res.getProperty ("ok", false);
}
