// Synthetic .sch fixture mimicking the private-DSL conventions the old
// regex reader handled: `superclass Parent` for inheritance, and
// `forward_declare class X;` for forward declarations. tree-sitter-cpp
// can't parse this so we route .sch through a small regex extractor
// that produces the same Entity / Relationship shape.

forward_declare class Engine;
forward_declare class FuelTank;

class Boat {
    superclass Vehicle

    void Launch();
    void Dock();

    Engine* m_pEngine;
    FuelTank m_fuelTank;
};
