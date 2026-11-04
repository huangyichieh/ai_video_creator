from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Condition, Event

import pytest

from story_channel.service_limits import LIMITS, limited, service_slot


def test_shared_provider_limits_allow_different_services_to_overlap():
    release = Event()
    condition = Condition()
    active = dict.fromkeys(LIMITS, 0)
    peak = active.copy()

    def run(service):
        with service_slot(service):
            with condition:
                active[service] += 1
                peak[service] = max(peak[service], active[service])
                condition.notify_all()
            try:
                assert release.wait(10)
            finally:
                with condition:
                    active[service] -= 1

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(run, service) for service in LIMITS for _ in range(4)]
        try:
            with condition:
                assert condition.wait_for(lambda: active == LIMITS, timeout=5)
                assert peak == LIMITS
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
    assert peak == LIMITS


def test_nested_provider_calls_do_not_deadlock_and_failures_release_slots():
    @limited("runway")
    def outer():
        with service_slot("runway"):
            raise ValueError("failed")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(ValueError, match="failed"):
            pool.submit(outer).result(timeout=2)
        assert pool.submit(lambda: next_call()).result(timeout=2)


def next_call():
    with service_slot("runway"):
        return True


def test_concurrent_openai_usage_stays_with_its_own_worker():
    from story_channel.llm import OpenAILLMProvider
    provider = OpenAILLMProvider(client=object())
    barrier = Barrier(2)

    def record(tokens):
        provider.last_usage = {"input_tokens": tokens}
        barrier.wait(timeout=5)
        return provider.last_usage["input_tokens"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(record, value) for value in (10, 20)]
        assert [future.result(timeout=5) for future in futures] == [10, 20]


def test_stopping_a_service_waiter_does_not_send_another_request():
    from story_channel.service_limits import task_context
    waiting, called, stop = Event(), Event(), Event()
    @limited("runway")
    def request():
        called.set()
    def worker():
        with task_context({"stop_event": stop}):
            waiting.set()
            request()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_slot("runway"):
            future = pool.submit(worker)
            assert waiting.wait(3)
            stop.set()
        with pytest.raises(InterruptedError):
            future.result(timeout=3)
    assert not called.is_set()
