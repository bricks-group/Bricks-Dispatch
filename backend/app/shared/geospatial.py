from typing import List
from .schemas import Coordinate

def is_point_in_polygon(lat: float, lng: float, polygon: List[Coordinate]) -> bool:
    """
    Ray-casting algorithm to determine if a point (lat, lng) is inside a polygon.
    Used for Bricks Area of Operations (AO) validation.
    """
    if not polygon or len(polygon) < 3:
        return False

    inside = False
    n = len(polygon)
    
    # Standard ray-casting algorithm
    p1 = polygon[0]
    for i in range(1, n + 1):
        p2 = polygon[i % n]
        if lng > min(p1.lng, p2.lng):
            if lng <= max(p1.lng, p2.lng):
                if lat <= max(p1.lat, p2.lat):
                    if p1.lng != p2.lng:
                        xints = (lng - p1.lng) * (p2.lat - p1.lat) / (p2.lng - p1.lng) + p1.lat
                    if p1.lat == p2.lat or lat <= xints:
                        inside = not inside
        p1 = p2

    return inside
