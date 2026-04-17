#pragma once
#include "Engine.hxx"
#include "FuelTank.hxx"
#include "Dashboard.hxx"

class Vehicle {
public:
    void Start();
    void Stop();
    void Accelerate(double speed);
    void Brake();
    void DisplayStatus();
    void Refuel(double liters);
    void CheckOil();
    void Navigate(double lat, double lon);
    void PlayMusic(const char* track);
    void AdjustSeat(int position);
private:
    Engine* m_pEngine;
    FuelTank m_fuelTank;
    Dashboard* m_pDashboard;
    double m_currentSpeed;
    double m_targetSpeed;
    bool m_isRunning;
    int m_seatPosition;
    char* m_currentTrack;
};
