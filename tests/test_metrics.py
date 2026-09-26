from cu import metrics


def _deliver():
    with metrics.span("native_delivery"):  # 2.0 .. 5.0
        pass


def test_nested_spans_count_their_time_once(monkeypatch):
    clock = iter([0.0, 1.0, 2.0, 5.0, 6.0, 10.0])
    monkeypatch.setattr(metrics.time, "monotonic", lambda: next(clock))
    measurement = metrics.Measurement()  # 0.0
    with metrics.collecting(measurement), metrics.span("native_convergence"):  # 1.0 .. 6.0
        _deliver()
    report = measurement.report(0)  # 10.0
    assert report["seconds"]["native_delivery"] == 3.0
    assert report["seconds"]["native_convergence"] == 2.0
    assert report["other_seconds"] == 5.0
    assert report["operations"] == {"native_convergence": 1, "native_delivery": 1}
