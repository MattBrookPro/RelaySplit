#include "PluginEditor.h"

RelaySplitEditor::RelaySplitEditor (RelaySplitProcessor& p)
    : AudioProcessorEditor (&p), proc (p)
{
    title.setText ("RelaySplit", juce::dontSendNotification);
    title.setFont (juce::Font (juce::FontOptions (20.0f)));
    addAndMakeVisible (title);

    userBox.setTextToShowWhenEmpty ("username", juce::Colours::grey);
    passBox.setTextToShowWhenEmpty ("password", juce::Colours::grey);
    passBox.setPasswordCharacter ((juce::juce_wchar) 0x2022);
    addAndMakeVisible (userBox);
    addAndMakeVisible (passBox);
    loginBtn.onClick = [this] { doLogin(); };
    addAndMakeVisible (loginBtn);
    loginErr.setColour (juce::Label::textColourId, juce::Colours::orangered);
    addAndMakeVisible (loginErr);
    logoutBtn.onClick = [this] { ControlClient::get().logout(); refresh(); };
    addChildComponent (logoutBtn);
    addChildComponent (accountLabel);

    // Source selection: "Broadcast my input" (default) or a peer's live broadcast.
    receiveLabel.setColour (juce::Label::textColourId, juce::Colours::grey);
    addChildComponent (receiveLabel);
    receiveBox.onChange = [this]
    {
        const int idx = receiveBox.getSelectedId() - 1;  // itemId is 1-based -> vector index
        if (idx >= 0 && idx < (int) receiveIds.size())
            proc.setReceiveChannel (receiveIds[(size_t) idx]);
        refresh();  // switching broadcast<->receive changes which section is shown
    };
    addChildComponent (receiveBox);
    receiveRefreshBtn.onClick = [this] { requestLiveRefresh(); };
    addChildComponent (receiveRefreshBtn);

    connectBtn.onClick = [this]
    {
        if (proc.connectionStatus() != WebRtcClient::Status::Disconnected) proc.disconnect();
        else                                                               proc.connect();
        timerCallback();  // reflect the new state immediately instead of waiting for the next tick
    };
    addAndMakeVisible (connectBtn);
    addAndMakeVisible (statusLabel);
    addAndMakeVisible (rttLabel);
    addAndMakeVisible (infLabel);

    matrixHeader.setText ("Share my broadcast with peers (click a name to assign):", juce::dontSendNotification);
    matrixHeader.setColour (juce::Label::textColourId, juce::Colours::grey);
    addChildComponent (matrixHeader);
    receiveHint.setText ("Receiving a peer's broadcast — your own input isn't sent.", juce::dontSendNotification);
    receiveHint.setColour (juce::Label::textColourId, juce::Colours::grey);
    addChildComponent (receiveHint);

    viewport.setViewedComponent (&matrix, false);
    viewport.setScrollBarsShown (true, true);
    addChildComponent (viewport);
    matrix.onChanged = [this] { refresh(); };

    InstanceRegistry::get().addChangeListener (this);
    setSize (580, 480);
    refresh();
    requestLiveRefresh();
    startTimerHz (5);
}

RelaySplitEditor::~RelaySplitEditor()
{
    stopTimer();
    InstanceRegistry::get().removeChangeListener (this);
}

void RelaySplitEditor::doLogin()
{
    const auto err = ControlClient::get().login (userBox.getText().trim(), passBox.getText());
    if (err.isNotEmpty()) loginErr.setText (err, juce::dontSendNotification);
    else { loginErr.setText ({}, juce::dontSendNotification); refresh(); requestLiveRefresh(); }
}

void RelaySplitEditor::requestLiveRefresh()
{
    if (! ControlClient::get().isLoggedIn()) { applyReceiveList ({}, {}); return; }

    bool expected = false;
    if (! liveRefreshing.compare_exchange_strong (expected, true)) return;  // one at a time

    juce::Component::SafePointer<RelaySplitEditor> safe (this);
    juce::Thread::launch ([safe]
    {
        // Network on a worker thread; the singleton ControlClient is process-wide and safe to use here.
        auto shared = ControlClient::get().sharedWithMe();
        auto live   = ControlClient::get().liveChannels();
        juce::MessageManager::callAsync ([safe, shared, live]
        {
            if (auto* e = safe.getComponent())
            {
                e->applyReceiveList (shared, live);
                e->liveRefreshing = false;
            }
        });
    });
}

void RelaySplitEditor::applyReceiveList (const std::vector<SharedBroadcast>& shared, const std::set<int>& live)
{
    const int prev = proc.getReceiveChannel();
    receiveIds.clear();
    receiveBox.clear (juce::dontSendNotification);
    receiveIds.push_back (0);
    receiveBox.addItem ("Broadcast my input", 1);   // itemId 1 -> index 0 -> channel 0

    int itemId = 2;
    for (auto& b : shared)
        if (live.count (b.id))   // only genuinely-live broadcasts
        {
            receiveIds.push_back (b.id);
            receiveBox.addItem (b.name + "  (" + b.owner + ")", itemId++);
        }

    int sel = 1;
    for (size_t i = 0; i < receiveIds.size(); ++i)
        if (receiveIds[i] == prev) sel = (int) i + 1;
    if (sel == 1 && prev != 0) proc.setReceiveChannel (0);  // the source we were on went offline
    receiveBox.setSelectedId (sel, juce::dontSendNotification);
    refresh();
}

void RelaySplitEditor::refresh()
{
    const bool in = ControlClient::get().isLoggedIn();
    const bool broadcasting = (proc.getReceiveChannel() == 0);

    userBox.setVisible (! in); passBox.setVisible (! in); loginBtn.setVisible (! in); loginErr.setVisible (! in);
    logoutBtn.setVisible (in); accountLabel.setVisible (in);
    receiveLabel.setVisible (in); receiveBox.setVisible (in); receiveRefreshBtn.setVisible (in);
    matrixHeader.setVisible (in && broadcasting); viewport.setVisible (in && broadcasting);
    receiveHint.setVisible (in && ! broadcasting);

    if (in)
        accountLabel.setText ("Signed in as " + ControlClient::get().getUsername()
                                  + "   —   " + proc.getInstanceName(),
                              juce::dontSendNotification);
    matrix.rebuild();
    resized();
}

void RelaySplitEditor::changeListenerCallback (juce::ChangeBroadcaster*) { refresh(); }

void RelaySplitEditor::paint (juce::Graphics& g)
{
    g.fillAll (getLookAndFeel().findColour (juce::ResizableWindow::backgroundColourId));
}

void RelaySplitEditor::resized()
{
    const bool in = ControlClient::get().isLoggedIn();
    const bool broadcasting = (proc.getReceiveChannel() == 0);

    auto r = getLocalBounds().reduced (12);
    title.setBounds (r.removeFromTop (28));
    r.removeFromTop (6);

    auto acct = r.removeFromTop (28);
    if (in)
    {
        logoutBtn.setBounds (acct.removeFromRight (90));
        accountLabel.setBounds (acct);
    }
    else
    {
        userBox.setBounds (acct.removeFromLeft (150));
        acct.removeFromLeft (8);
        passBox.setBounds (acct.removeFromLeft (150));
        acct.removeFromLeft (8);
        loginBtn.setBounds (acct.removeFromLeft (80));
        acct.removeFromLeft (8);
        loginErr.setBounds (acct);
    }
    r.removeFromTop (8);

    if (in)  // source selection
    {
        auto rcv = r.removeFromTop (28);
        receiveLabel.setBounds (rcv.removeFromLeft (70));
        receiveRefreshBtn.setBounds (rcv.removeFromRight (74));
        rcv.removeFromRight (6);
        receiveBox.setBounds (rcv);
        r.removeFromTop (8);
    }

    auto conn = r.removeFromTop (28);
    connectBtn.setBounds (conn.removeFromLeft (110));
    conn.removeFromLeft (10);
    statusLabel.setBounds (conn.removeFromLeft (110));
    rttLabel.setBounds (conn.removeFromLeft (110));
    infLabel.setBounds (conn.removeFromLeft (150));
    r.removeFromTop (10);

    if (in && broadcasting)
    {
        matrixHeader.setBounds (r.removeFromTop (20));
        r.removeFromTop (4);
        viewport.setBounds (r);
        matrix.setSize (juce::jmax (r.getWidth() - 16, matrix.getWidth()), matrix.getHeight());
    }
    else if (in)
    {
        receiveHint.setBounds (r.removeFromTop (24));
    }
}

void RelaySplitEditor::timerCallback()
{
    using S = WebRtcClient::Status;
    const auto st = proc.connectionStatus();
    const bool receiving = proc.getReceiveChannel() > 0;

    switch (st)
    {
        case S::Connected:
            statusLabel.setText (receiving ? "receiving" : "broadcasting", juce::dontSendNotification);
            connectBtn.setButtonText ("Disconnect");
            break;
        case S::Connecting:
            statusLabel.setText ("connecting...", juce::dontSendNotification);
            connectBtn.setButtonText ("Cancel");
            break;
        default:
            statusLabel.setText ("disconnected", juce::dontSendNotification);
            connectBtn.setButtonText ("Connect");
            break;
    }
    rttLabel.setText ("RTT: " + juce::String (proc.rttMs(), 0) + " ms", juce::dontSendNotification);
    infLabel.setText ("inference: " + juce::String (proc.inferenceMs(), 0) + " ms", juce::dontSendNotification);

    // Mode/source can't change mid-connection — lock the selector while busy.
    const bool busy = (st != S::Disconnected);
    receiveBox.setEnabled (! busy);
    receiveRefreshBtn.setEnabled (! busy);

    // Keep the live-source list current (~ every 3 s) so removed broadcasters drop out. Skip while
    // connected/connecting so the list can't shuffle under the user.
    if (++liveTick >= 15) { liveTick = 0; if (! busy) requestLiveRefresh(); }
}
