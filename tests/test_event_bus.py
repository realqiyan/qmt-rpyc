import pytest
from qmt_rpyc.adapters.xtquant_2_0_6_1.events import EventBus, Subscription, _MAX_EVENTS_PER_SUB


class TestSubscription:
    def test_push_and_drain(self):
        sub = Subscription("s1", ["order"], None)
        sub.push({"type": "order", "data": {"id": 1}})
        sub.push({"type": "order", "data": {"id": 2}})
        events, dropped = sub.drain()
        assert len(events) == 2
        assert dropped == 0
        assert events[0]["data"]["id"] == 1

    def test_drain_empty(self):
        sub = Subscription("s1", ["order"], None)
        events, dropped = sub.drain()
        assert events == []
        assert dropped == 0

    def test_filter_by_type(self):
        sub = Subscription("s1", ["order"], None)
        sub.push({"type": "order", "data": 1})
        sub.push({"type": "trade", "data": 2})
        events, _ = sub.drain()
        assert len(events) == 1
        assert events[0]["type"] == "order"

    def test_filter_by_account(self):
        sub = Subscription("s1", ["order"], "ACC1")
        sub.push({"type": "order", "account_id": "ACC1", "data": 1})
        sub.push({"type": "order", "account_id": "ACC2", "data": 2})
        events, _ = sub.drain()
        assert len(events) == 1
        assert events[0]["account_id"] == "ACC1"

    def test_drop_count_when_full(self):
        sub = Subscription("s1", ["order"], None, max_events=3)
        for i in range(5):
            sub.push({"type": "order", "data": i})
        events, dropped = sub.drain()
        assert len(events) == 3
        assert dropped == 2

    def test_drain_resets_drop_count(self):
        sub = Subscription("s1", ["order"], None, max_events=2)
        for i in range(4):
            sub.push({"type": "order", "data": i})
        _, dropped1 = sub.drain()
        assert dropped1 == 2
        sub.push({"type": "order", "data": 99})
        _, dropped2 = sub.drain()
        assert dropped2 == 0


class TestEventBus:
    def test_subscribe_returns_id(self):
        bus = EventBus()
        sub_id = bus.subscribe(["order"])
        assert isinstance(sub_id, str)
        assert len(sub_id) > 0

    def test_publish_to_matching_sub(self):
        bus = EventBus()
        sub_id = bus.subscribe(["order"])
        bus.publish({"type": "order", "data": 1})
        sub = bus.get_subscription(sub_id)
        events, _ = sub.drain()
        assert len(events) == 1

    def test_no_delivery_to_wrong_type(self):
        bus = EventBus()
        sub_id = bus.subscribe(["order"])
        bus.publish({"type": "trade", "data": 1})
        sub = bus.get_subscription(sub_id)
        events, _ = sub.drain()
        assert len(events) == 0

    def test_unsubscribe(self):
        bus = EventBus()
        sub_id = bus.subscribe(["order"])
        assert bus.unsubscribe(sub_id) is True
        assert bus.get_subscription(sub_id) is None
        assert bus.unsubscribe("nonexistent") is False

    def test_publish_to_multiple_subs(self):
        bus = EventBus()
        s1 = bus.subscribe(["order"])
        s2 = bus.subscribe(["order", "trade"])
        bus.publish({"type": "order", "data": 1})
        assert len(bus.get_subscription(s1).drain()[0]) == 1
        assert len(bus.get_subscription(s2).drain()[0]) == 1
