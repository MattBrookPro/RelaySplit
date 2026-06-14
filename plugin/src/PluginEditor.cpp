#include "PluginEditor.h"

RelaySplitEditor::RelaySplitEditor (RelaySplitProcessor& p)
    : AudioProcessorEditor (&p), proc (p)
{
    title.setText ("RelaySplit", juce::dontSendNotification);
    title.setFont (juce::Font (juce::FontOptions (22.0f)));
    addAndMakeVisible (title);

    statusLabel.setText ("disconnected", juce::dontSendNotification);
    addAndMakeVisible (statusLabel);
    addAndMakeVisible (rttLabel);
    addAndMakeVisible (infLabel);

    connectButton.onClick = [this]
    {
        // Phase 2: start/stop the WebRTC session against the Modal container.
        // Phase 1 stub just toggles the flag so the UI is exercised.
        proc.connected = ! proc.connected.load();
    };
    addAndMakeVisible (connectButton);

    setSize (320, 200);
    startTimerHz (10);
}

RelaySplitEditor::~RelaySplitEditor() { stopTimer(); }

void RelaySplitEditor::paint (juce::Graphics& g)
{
    g.fillAll (getLookAndFeel().findColour (juce::ResizableWindow::backgroundColourId));
}

void RelaySplitEditor::resized()
{
    auto r = getLocalBounds().reduced (16);
    title.setBounds (r.removeFromTop (32));
    connectButton.setBounds (r.removeFromTop (32).removeFromLeft (120));
    r.removeFromTop (12);
    statusLabel.setBounds (r.removeFromTop (24));
    rttLabel.setBounds (r.removeFromTop (24));
    infLabel.setBounds (r.removeFromTop (24));
}

void RelaySplitEditor::timerCallback()
{
    const bool c = proc.connected.load();
    statusLabel.setText (c ? "connected" : "disconnected", juce::dontSendNotification);
    rttLabel.setText ("net RTT: "   + juce::String (proc.netRttMs.load(),    0) + " ms", juce::dontSendNotification);
    infLabel.setText ("inference: " + juce::String (proc.inferenceMs.load(), 0) + " ms", juce::dontSendNotification);
    connectButton.setButtonText (c ? "Disconnect" : "Connect");
}
