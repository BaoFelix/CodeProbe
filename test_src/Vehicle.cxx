#include "Vehicle.hxx"

void Vehicle::Start() {
    m_pEngine->Ignite();
    m_isRunning = true;
    m_pDashboard->Update();
}

void Vehicle::Stop() {
    m_pEngine->Shutdown();
    m_isRunning = false;
}

void Vehicle::Accelerate(double speed) {
    m_targetSpeed = speed;
    m_pEngine->SetThrottle(speed / 200.0);
    m_currentSpeed = speed;
    m_pDashboard->ShowSpeed(m_currentSpeed);
}

void Vehicle::Refuel(double liters) {
    m_fuelTank.Fill(liters);
    m_pDashboard->ShowFuelLevel(m_fuelTank.GetLevel());
}

void Vehicle::DisplayStatus() {
    m_pDashboard->ShowSpeed(m_currentSpeed);
    m_pDashboard->ShowFuelLevel(m_fuelTank.GetLevel());
    m_pDashboard->ShowRPM(m_pEngine->GetRPM());
}
