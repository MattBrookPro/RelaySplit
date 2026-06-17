#pragma once
#include <juce_core/juce_core.h>
#include <vector>

// Lock-free single-producer/single-consumer FIFO of interleaved stereo float frames. It is the
// audio<->network thread boundary: the audio thread pushes captured input / pops separated output;
// the WebRTC worker does the opposite. juce::AbstractFifo gives a wait-free index handoff, so
// push/pop never lock or allocate — the cardinal rule for the audio callback.
class StereoFifo
{
public:
    explicit StereoFifo (int capacityFrames = 48000)
        : fifo (capacityFrames), buffer ((size_t) capacityFrames * 2, 0.0f) {}

    // Write `numFrames` of interleaved [L,R,L,R,...]; returns frames actually written (may be < if full).
    int push (const float* interleaved, int numFrames)
    {
        const auto h = fifo.write (numFrames);
        copyIn (interleaved, 0, h.startIndex1, h.blockSize1);
        copyIn (interleaved, h.blockSize1, h.startIndex2, h.blockSize2);
        return h.blockSize1 + h.blockSize2;
    }

    // Read up to `numFrames` into interleaved `out`; returns frames read (rest of `out` is untouched).
    int pop (float* out, int numFrames)
    {
        const auto h = fifo.read (numFrames);
        copyOut (out, 0, h.startIndex1, h.blockSize1);
        copyOut (out, h.blockSize1, h.startIndex2, h.blockSize2);
        return h.blockSize1 + h.blockSize2;
    }

    int numReady() const { return fifo.getNumReady(); }
    int freeSpace() const { return fifo.getFreeSpace(); }

private:
    void copyIn (const float* src, int srcFrameOffset, int start, int n)
    {
        for (int i = 0; i < n; ++i)
        {
            buffer[(size_t) (start + i) * 2]     = src[(size_t) (srcFrameOffset + i) * 2];
            buffer[(size_t) (start + i) * 2 + 1] = src[(size_t) (srcFrameOffset + i) * 2 + 1];
        }
    }
    void copyOut (float* dst, int dstFrameOffset, int start, int n)
    {
        for (int i = 0; i < n; ++i)
        {
            dst[(size_t) (dstFrameOffset + i) * 2]     = buffer[(size_t) (start + i) * 2];
            dst[(size_t) (dstFrameOffset + i) * 2 + 1] = buffer[(size_t) (start + i) * 2 + 1];
        }
    }

    juce::AbstractFifo fifo;     // counts frames (items); 2 floats stored per item
    std::vector<float> buffer;
};
