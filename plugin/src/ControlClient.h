#pragma once
#include <juce_core/juce_core.h>
#include <set>
#include <vector>

struct Peer { int id = 0; juce::String name; };

// Shared (process-wide) session to the RelaySplit control plane. One account per DAW process — all
// plugin instances use it. Calls block on the network, so invoke from the message thread on user
// actions (they're infrequent). Peers are cached after login.
class ControlClient
{
public:
    static ControlClient& get();
    static constexpr const char* kBase = "https://relaysplit.vaguelystrange.com";

    bool isLoggedIn() const { return token.isNotEmpty(); }
    juce::String getUsername() const { return username; }

    juce::String login (const juce::String& user, const juce::String& pass);  // "" ok, else error msg
    void logout();

    const std::vector<Peer>& peers (bool forceRefresh = false);
    int  createChannel (const juce::String& name, const juce::String& stem = "vocals");  // id, or 0
    std::set<int> getShares (int channelId);
    bool setShares (int channelId, const std::set<int>& peerIds);

private:
    juce::var get (const juce::String& path);
    juce::var post (const juce::String& path, const juce::var& body);

    juce::String token, username;
    std::vector<Peer> cachedPeers;
};
