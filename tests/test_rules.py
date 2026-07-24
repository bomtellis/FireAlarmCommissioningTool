from shapely.geometry import Polygon

from firepanel.rules import ZoneShape, find_adjacencies


def test_cardinal_and_vertical_adjacency() -> None:
    shapes = [
        ZoneShape(1, 3, 2, Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])),
        ZoneShape(2, 3, 2, Polygon([(10, 0), (20, 0), (20, 10), (10, 10)])),
        ZoneShape(4, 2, 1, Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])),
        ZoneShape(9, 1, 0, Polygon([(100, 100), (110, 100), (110, 110), (100, 110)])),
    ]
    result = find_adjacencies(shapes, horizontal_tolerance=0.01)
    assert result[1][2] == "adjacent on same floor"
    assert result[1][4] == "directly above/below"
    assert 9 not in result[1]
