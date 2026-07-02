#ifndef DIAGNOSTIC_TOOL_HXX
#define DIAGNOSTIC_TOOL_HXX

// Fixture for body-call depends edges: Vehicle uses this class ONLY inside
// Vehicle.cxx method bodies (local, static call, new, cast) — no field, no
// signature. Before parser v4 this coupling was invisible.
class DiagnosticTool {
public:
    static DiagnosticTool& Instance();
    void RunChecks();
    double Measure() const;
};

#endif
