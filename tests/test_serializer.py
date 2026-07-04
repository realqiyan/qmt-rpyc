import numpy as np
import pandas as pd
import pytest
from server.serializer import serialize


class TestPrimitives:
    def test_none(self):
        assert serialize(None) is None

    def test_bool(self):
        assert serialize(True) is True
        assert serialize(False) is False

    def test_int_float_str(self):
        assert serialize(42) == 42
        assert serialize(3.14) == 3.14
        assert serialize("hello") == "hello"


class TestNumpy:
    def test_numpy_integer(self):
        assert serialize(np.int64(42)) == 42
        assert isinstance(serialize(np.int64(42)), int)

    def test_numpy_floating(self):
        assert serialize(np.float64(3.14)) == 3.14
        assert isinstance(serialize(np.float64(3.14)), float)

    def test_numpy_array(self):
        arr = np.array([1, 2, 3])
        result = serialize(arr)
        assert result == [1, 2, 3]
        assert isinstance(result, list)


class TestPandas:
    def test_dataframe(self):
        df = pd.DataFrame({"open": [10.0, 11.0], "close": [10.5, 11.5]})
        result = serialize(df)
        assert result["columns"] == ["open", "close"]
        assert result["data"] == [[10.0, 10.5], [11.0, 11.5]]
        assert len(result["index"]) == 2

    def test_dataframe_reconstruct(self):
        df = pd.DataFrame({"a": [1, 2]})
        result = serialize(df)
        df2 = pd.DataFrame(**result)
        assert list(df2.columns) == ["a"]
        assert df2.iloc[0, 0] == 1


class TestContainers:
    def test_dict(self):
        assert serialize({"a": 1, "b": [2, 3]}) == {"a": 1, "b": [2, 3]}

    def test_list(self):
        assert serialize([1, "two", 3.0]) == [1, "two", 3.0]

    def test_tuple_becomes_list(self):
        result = serialize((1, 2, 3))
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_nested(self):
        data = {"items": [{"id": 1}, {"id": 2}]}
        assert serialize(data) == {"items": [{"id": 1}, {"id": 2}]}


class TestObjects:
    def test_object_with_dict(self):
        class Dummy:
            def __init__(self):
                self.name = "test"
                self.value = 42
                self._private = "hidden"
        result = serialize(Dummy())
        assert result == {"name": "test", "value": 42}

    def test_depth_limit(self):
        nested = []
        current = nested
        for _ in range(70):
            new_list = []
            current.append(new_list)
            current = new_list
        result = serialize(nested)
        assert "<serialization depth exceeded>" in str(result)


class TestCExtensionLike:
    def test_object_without_dict(self):
        class NoDict:
            __slots__ = ["x", "y"]
            def __init__(self):
                self.x = 1
                self.y = 2
        result = serialize(NoDict())
        assert result == {"x": 1, "y": 2}
