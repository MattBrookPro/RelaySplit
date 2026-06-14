#include "InstanceRegistry.h"
#include <algorithm>

InstanceRegistry& InstanceRegistry::get() { static InstanceRegistry r; return r; }

void InstanceRegistry::add (RelaySplitProcessor* p)
{
    { std::lock_guard<std::mutex> l (m); instances.push_back (p); }
    sendChangeMessage();
}

void InstanceRegistry::remove (RelaySplitProcessor* p)
{
    { std::lock_guard<std::mutex> l (m); instances.erase (std::remove (instances.begin(), instances.end(), p), instances.end()); }
    sendChangeMessage();
}

std::vector<RelaySplitProcessor*> InstanceRegistry::snapshot()
{
    std::lock_guard<std::mutex> l (m);
    return instances;
}
