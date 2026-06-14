# RelaySplit JUCE plugin — native WebRTC client to the Modal GPU separator.
#
# Phase 1 (this file): a plugin that BUILDS (VST3 + Standalone) with the real-time architecture and
# latency-meter UI scaffolded. Phase 2 adds the WebRTC client (libdatachannel + Opus) — see README.
cmake_minimum_required(VERSION 3.22)
project(RelaySplitPlugin VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)
# Pin JUCE so the build is reproducible. Shallow clone keeps the fetch small.
FetchContent_Declare(JUCE
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 8.0.4
    GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(JUCE)

# Phase 2 (the native WebRTC client) needs libdatachannel + Opus. Wiring those in hit a real
# dependency-integration blocker on this toolchain (CMake 4.2 + libdatachannel's bundled libsrtp
# can't find the bundled mbedTLS via find_package). It's documented with resolution options in
# PHASE2.md and kept OUT of the default build so the plugin foundation always builds cleanly.

juce_add_plugin(RelaySplit
    PRODUCT_NAME "RelaySplit"
    COMPANY_NAME "MattBrookPro"
    PLUGIN_MANUFACTURER_CODE Mbpr
    PLUGIN_CODE Rspl
    FORMATS VST3 Standalone
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE)

target_sources(RelaySplit PRIVATE
    src/PluginProcessor.cpp
    src/PluginEditor.cpp)

target_compile_definitions(RelaySplit PUBLIC
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0)

target_link_libraries(RelaySplit
    PRIVATE
        juce::juce_audio_utils
        juce::juce_dsp
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_warning_flags)
