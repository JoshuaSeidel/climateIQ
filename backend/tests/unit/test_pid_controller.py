from backend.core.pid_controller import PIDConfig, PIDController


def test_pid_basic_response() -> None:
    pid = PIDController(PIDConfig(kp=1.0, ki=0.0, kd=0.0, output_min=0, output_max=1))
    output = pid.compute(setpoint=22.0, measurement=20.0, timestamp=0.0)
    assert 0 <= output <= 1


def test_pid_converges_with_integral() -> None:
    pid = PIDController(
        PIDConfig(kp=0.4, ki=0.1, kd=0.0, output_min=0, output_max=1, sample_time=0)
    )
    outputs = [pid.compute(22.0, 20.0, timestamp=t) for t in range(1, 6)]
    assert outputs[-1] >= outputs[0]
