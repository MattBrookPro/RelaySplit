#pragma once
#include <juce_core/juce_core.h>
#include <set>
#include <vector>

struct Peer { int id = 0; juce::String name; };
struct SharedBroadcast { int id = 0; juce::String name, owner; };  // a peer's broadcast I may receive

// Shared (process-wide) session to the RelaySplit control plane. One account per DAW process — all
// plugin instances use it. Calls block on the network, so invoke from the message thread on user
// actions (they're infrequent). Peers are cached after login.
class ControlClient
{
public:
    static ControlClient& get();
    static constexpr const char* kBase = "https://relaysplit.vaguelystrange.com";
    static constexpr const char* kLiveBase = "https://blitzncs--relaysplit-live-web.modal.run";

    bool isLoggedIn() const { return token.isNotEmpty(); }
    juce::String getUsername() const { return username; }

    juce::String login (const juce::String& user, const juce::String& pass);  // "" ok, else error msg
    void logout();

    const std::vector<Peer>& peers (bool forceRefresh = false);
    int  createChannel (const juce::String& name, const juce::String& stem = "vocals");  // id, or 0
    std::set<int> getShares (int channelId);
    bool setShares (int channelId, const std::set<int>& peerIds);
    std::vector<SharedBroadcast> sharedWithMe();  // broadcasts peers have shared with me (to tune into)
    std::set<int> liveChannels();                 // channel ids currently broadcasting (from the GPU /live)

private:
    ControlClient() { loadSession(); }  // restore a saved login so it persists across DAW restarts

    juce::var get (const juce::String& path);
    juce::var post (const juce::String& path, const juce::var& body);
    void maybeHandleAuthError (const juce::var& res);  // a 401 from a stale token -> auto sign-out
    void loadSession();
    void saveSession();
    void clearSession();

    juce::String token, username;
    std::vector<Peer> cachedPeers;
};
