#pragma once

class FuelTank {
public:
    double GetLevel();
    void Fill(double liters);
    bool IsEmpty();
    double Consume(double amount);
private:
    double m_capacity;
    double m_currentLevel;
};
