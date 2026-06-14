#pragma once
#include <juce_audio_utils/juce_audio_utils.h>
#include <atomic>
#include <set>
#include <vector>
#include "PluginProcessor.h"
#include "PeerMatrix.h"
#include "ControlClient.h"

// Editor laid out top-to-bottom in the order you use it: sign in → choose what to listen to
// (your own input, or a peer's live broadcast) → connect → (when broadcasting) share with peers.
// The "Listen to" list shows only broadcasts that are LIVE right now, refreshed off the message
// thread so a stopped broadcaster drops out on its own.
class RelaySplitEditor : public juce::AudioProcessorEditor,
                         private juce::Timer,
                         private juce::ChangeListener
{
public:
    explicit RelaySplitEditor (RelaySplitProcessor&);
    ~RelaySplitEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void timerCallback() override;
    void changeListenerCallback (juce::ChangeBroadcaster*) override;
    void refresh();
    void doLogin();
    void requestLiveRefresh();   // fetch shared-with-me ∩ live on a worker thread, then update the box
    void applyReceiveList (const std::vector<SharedBroadcast>& shared, const std::set<int>& live);

    RelaySplitProcessor& proc;

    juce::Label title;
    // account
    juce::TextEditor userBox, passBox;
    juce::TextButton loginBtn { "Log in" }, logoutBtn { "Log out" };
    juce::Label accountLabel, loginErr;
    // source selection (receive mode)
    juce::Label receiveLabel { {}, "Listen to:" };
    juce::ComboBox receiveBox;
    juce::TextButton receiveRefreshBtn { "Refresh" };
    std::vector<int> receiveIds;   // combo index -> channel id (0 = broadcast my input)
    // connection
    juce::TextButton connectBtn { "Connect" };
    juce::Label statusLabel, rttLabel, infLabel;
    // peer sharing (broadcast mode only) / receive hint
    juce::Label matrixHeader, receiveHint;
    juce::Viewport viewport;
    PeerMatrix matrix;

    std::atomic<bool> liveRefreshing { false };  // guards one in-flight background refresh
    int liveTick = 0;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (RelaySplitEditor)
};
