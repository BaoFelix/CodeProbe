#pragma once

class Engine {
public:
    void Ignite();
    void Shutdown();
    double GetRPM();
    void SetThrottle(double level);
private:
    double m_rpm;
    double m_throttle;
    bool m_isRunning;
    double m_temperature;
};
