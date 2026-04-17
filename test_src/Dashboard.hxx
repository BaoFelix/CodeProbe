#pragma once
#include "Engine.hxx"
#include "FuelTank.hxx"

class Dashboard {
public:
    void Update();
    void ShowSpeed(double speed);
    void ShowFuelLevel(double level);
    void ShowRPM(double rpm);
    void ShowWarning(const char* message);
private:
    Engine* m_pEngine;
    FuelTank* m_pFuelTank;
    bool m_warningActive;
};
