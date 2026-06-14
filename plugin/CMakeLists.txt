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

# WebRTC stack via vcpkg: libdatachannel built with the srtp/media feature + OpenSSL (sidesteps the
# bundled-mbedTLS/libsrtp wall hit by FetchContent). Configure with the vcpkg toolchain, e.g.:
#   -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake
find_package(LibDataChannel CONFIG REQUIRED)
find_package(Opus CONFIG REQUIRED)

juce_add_plugin(RelaySplit
    PRODUCT_NAME "RelaySplit"
    COMPANY_NAME "MattBrookPro"
    PLUGIN_MANUFACTURER_CODE Mbpr
    PLUGIN_CODE Rspl
    FORMATS VST3 Standalone
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    VST3_AUTO_MANIFEST FALSE  # skip the load-to-generate moduleinfo step (it can't find the vcpkg DLLs)
    COPY_PLUGIN_AFTER_BUILD FALSE)

target_sources(RelaySplit PRIVATE
    src/PluginProcessor.cpp
    src/PluginEditor.cpp
    src/WebRtcClient.cpp)

target_compile_definitions(RelaySplit PUBLIC
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0
    RTC_ENABLE_MEDIA=1)

target_link_libraries(RelaySplit
    PRIVATE
        juce::juce_audio_utils
        juce::juce_dsp
        LibDataChannel::LibDataChannel
        Opus::opus
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_warning_flags)

# libdatachannel.dll exports std::vector<std::byte> symbols that also live in juce_core.obj — the
# same instantiation, so let the linker pick one (propagates to the VST3/Standalone exe links).
if (MSVC)
    target_link_options(RelaySplit INTERFACE "/FORCE:MULTIPLE")
endif()
