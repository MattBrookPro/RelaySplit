#pragma once
#include <juce_gui_basics/juce_gui_basics.h>
#include "PluginProcessor.h"
#include "ControlClient.h"
#include "InstanceRegistry.h"

// The peer-assignment matrix: one row per RelaySplit instance in the session, a peer chip per
// account peer (click to assign/unassign that peer to that instance's broadcast), a per-row select
// toggle, and a top "group apply" row that toggles a peer across all selected instances at once.
class PeerMatrix : public juce::Component
{
public:
    std::function<void()> onChanged;

    void rebuild()
    {
        groupChips.clear(); selects.clear(); names.clear(); chips.clear();

        const auto peers = ControlClient::get().peers();   // cached after login
        const auto insts = InstanceRegistry::get().snapshot();
        P = (int) peers.size();
        rows = (int) insts.size();

        groupLabel.setText ("Group apply:", juce::dontSendNotification);
        groupLabel.setColour (juce::Label::textColourId, juce::Colours::grey);
        addAndMakeVisible (groupLabel);
        for (auto& pr : peers)
        {
            auto* g = new juce::TextButton (pr.name);
            styleChip (g, false);
            g->onClick = [this, id = pr.id] { groupToggle (id); };
            groupChips.add (g); addAndMakeVisible (g);
        }

        for (auto* proc : insts)
        {
            auto* sel = new juce::ToggleButton();
            sel->setToggleState (proc->isGroupSelected(), juce::dontSendNotification);
            sel->onClick = [proc, sel] { proc->setGroupSelected (sel->getToggleState()); };
            selects.add (sel); addAndMakeVisible (sel);

            auto* nm = new juce::Label ({}, proc->getInstanceName());
            names.add (nm); addAndMakeVisible (nm);

            for (auto& pr : peers)
            {
                auto* chip = new juce::TextButton (pr.name);  // green = assigned (no glyphs → no tofu)
                styleChip (chip, proc->getAssignedPeers().count (pr.id) > 0);
                chip->onClick = [this, proc, id = pr.id]
                {
                    auto s = proc->getAssignedPeers();
                    if (s.count (id)) s.erase (id); else s.insert (id);
                    proc->setAssignedPeers (s);
                    if (onChanged) onChanged();
                };
                chips.add (chip); addAndMakeVisible (chip);
            }
        }

        setSize (juce::jmax (getParentWidth(), leftW + P * chipW + 12), (rows + 1) * rowH + 8);
        resized();
    }

    void resized() override
    {
        groupLabel.setBounds (0, 0, leftW - 4, rowH - 4);
        for (int i = 0; i < groupChips.size(); ++i)
            groupChips[i]->setBounds (leftW + i * chipW, 0, chipW - 4, rowH - 4);

        for (int r = 0; r < rows; ++r)
        {
            const int y = (r + 1) * rowH;
            selects[r]->setBounds (2, y, 22, rowH - 4);
            names[r]->setBounds (26, y, leftW - 28, rowH - 4);
            for (int i = 0; i < P; ++i)
                chips[r * P + i]->setBounds (leftW + i * chipW, y, chipW - 4, rowH - 4);
        }
    }

private:
    static void styleChip (juce::TextButton* b, bool on)
    {
        b->setColour (juce::TextButton::buttonColourId, on ? juce::Colour (0xff3ad29f) : juce::Colour (0xff2a2f3a));
        b->setColour (juce::TextButton::textColourOffId, on ? juce::Colours::black : juce::Colours::lightgrey);
    }

    void groupToggle (int peerId)
    {
        auto insts = InstanceRegistry::get().snapshot();
        bool any = false, allHave = true;
        for (auto* p : insts) if (p->isGroupSelected()) { any = true; if (! p->getAssignedPeers().count (peerId)) allHave = false; }
        if (! any) return;
        for (auto* p : insts) if (p->isGroupSelected())
        {
            auto s = p->getAssignedPeers();
            if (allHave) s.erase (peerId); else s.insert (peerId);
            p->setAssignedPeers (s);
        }
        if (onChanged) onChanged();
    }

    juce::Label groupLabel;
    juce::OwnedArray<juce::TextButton> groupChips, chips;
    juce::OwnedArray<juce::ToggleButton> selects;
    juce::OwnedArray<juce::Label> names;
    int P = 0, rows = 0;
    static constexpr int chipW = 96, rowH = 30, leftW = 160;
};
