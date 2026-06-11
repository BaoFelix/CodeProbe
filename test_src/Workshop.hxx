#pragma once
#include "Engine.hxx"
#include "FuelTank.hxx"
#include <vector>
#include <memory>

// Designed to exercise the new entity-relationship model:
//   - namespace nesting → qualified names
//   - struct as entity
//   - interface (I-prefixed pure-virtual) → implements relation
//   - inner class → parent_qname linkage
//   - varied field shapes → composes / aggregates / associates

namespace Garage {

class ILogger {
public:
    virtual ~ILogger() = default;
    virtual void log(const char* msg) = 0;
};

struct ToolSet {
    int wrenchCount;
    int screwdriverCount;
};

class Workshop : public ILogger {
public:
    Workshop();
    void Open();
    void Close();
    void Repair(Engine& e);
    void log(const char* msg) override;

    class Receipt {                                  // inner class
    public:
        double total;
        const char* customerName;
    };

private:
    std::unique_ptr<Engine>     m_primaryEngine;     // composes (Lv-4)
    FuelTank                    m_spareTank;         // composes (Lv-4) — value field
    std::vector<Engine*>        m_loaners;           // aggregates (Lv-3)
    Engine*                     m_borrowedEngine;    // associates (Lv-1)
    ToolSet                     m_tools;             // composes (Lv-4)
};

}  // namespace Garage
