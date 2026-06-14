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

    connectBtn.onClick = [this] { if (proc.isConnected()) proc.disconnect(); else proc.connect(); };
    addAndMakeVisible (connectBtn);
    addAndMakeVisible (statusLabel);
    addAndMakeVisible (rttLabel);
    addAndMakeVisible (infLabel);

    // Receive mode: pick a peer's broadcast to tune into (item 1 = broadcast my own input instead).
    receiveLabel.setColour (juce::Label::textColourId, juce::Colours::grey);
    addAndMakeVisible (receiveLabel);
    receiveBox.onChange = [this]
    {
        const int idx = receiveBox.getSelectedId() - 1;  // itemId is 1-based -> vector index
        if (idx >= 0 && idx < (int) receiveIds.size())
            proc.setReceiveChannel (receiveIds[(size_t) idx]);
    };
    addAndMakeVisible (receiveBox);
    receiveRefreshBtn.onClick = [this] { populateReceive(); };
    addAndMakeVisible (receiveRefreshBtn);

    viewport.setViewedComponent (&matrix, false);
    viewport.setScrollBarsShown (true, true);
    addAndMakeVisible (viewport);
    matrix.onChanged = [this] { refresh(); };

    InstanceRegistry::get().addChangeListener (this);
    setSize (580, 480);
    populateReceive();
    refresh();
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
    else { loginErr.setText ({}, juce::dontSendNotification); populateReceive(); refresh(); }
}

void RelaySplitEditor::populateReceive()
{
    // Network GET, so only when the user asks (login / ↻) — not on every UI refresh.
    receiveIds.clear();
    receiveBox.clear (juce::dontSendNotification);
    receiveIds.push_back (0);
    receiveBox.addItem ("Broadcast my input", 1);  // itemId 1 -> index 0 -> channel 0 (broadcaster)
    if (ControlClient::get().isLoggedIn())
    {
        int itemId = 2;
        for (auto& b : ControlClient::get().sharedWithMe())
        {
            receiveIds.push_back (b.id);
            receiveBox.addItem ("Receive: " + b.name + "  (" + b.owner + ")", itemId++);
        }
    }
    int sel = 1;  // reflect the processor's current choice
    for (size_t i = 0; i < receiveIds.size(); ++i)
        if (receiveIds[i] == proc.getReceiveChannel()) sel = (int) i + 1;
    receiveBox.setSelectedId (sel, juce::dontSendNotification);
}

void RelaySplitEditor::refresh()
{
    const bool in = ControlClient::get().isLoggedIn();
    userBox.setVisible (! in); passBox.setVisible (! in); loginBtn.setVisible (! in); loginErr.setVisible (! in);
    logoutBtn.setVisible (in); accountLabel.setVisible (in);
    if (in)
        accountLabel.setText ("Signed in as " + ControlClient::get().getUsername()
                                  + "  ·  this instance: " + proc.getInstanceName(),
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
    auto r = getLocalBounds().reduced (12);
    title.setBounds (r.removeFromTop (28));
    r.removeFromTop (6);

    auto acct = r.removeFromTop (28);
    if (ControlClient::get().isLoggedIn())
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
    auto conn = r.removeFromTop (28);
    connectBtn.setBounds (conn.removeFromLeft (100));
    conn.removeFromLeft (10);
    statusLabel.setBounds (conn.removeFromLeft (120));
    rttLabel.setBounds (conn.removeFromLeft (130));
    infLabel.setBounds (conn.removeFromLeft (150));

    r.removeFromTop (8);
    auto rcv = r.removeFromTop (28);
    receiveLabel.setBounds (rcv.removeFromLeft (70));
    receiveRefreshBtn.setBounds (rcv.removeFromRight (32));
    rcv.removeFromRight (6);
    receiveBox.setBounds (rcv);

    r.removeFromTop (8);
    viewport.setBounds (r);
    matrix.setSize (juce::jmax (r.getWidth() - 16, matrix.getWidth()), matrix.getHeight());
}

void RelaySplitEditor::timerCallback()
{
    const bool c = proc.isConnected();
    const bool receiving = proc.getReceiveChannel() > 0;
    statusLabel.setText (c ? (receiving ? "receiving" : "broadcasting") : "disconnected",
                         juce::dontSendNotification);
    rttLabel.setText ("RTT: " + juce::String (proc.rttMs(), 0) + " ms", juce::dontSendNotification);
    infLabel.setText ("inference: " + juce::String (proc.inferenceMs(), 0) + " ms", juce::dontSendNotification);
    connectBtn.setButtonText (c ? "Disconnect" : "Connect");
    // Mode can't change mid-connection — lock the selector while connected.
    receiveBox.setEnabled (! c);
    receiveRefreshBtn.setEnabled (! c);
}
