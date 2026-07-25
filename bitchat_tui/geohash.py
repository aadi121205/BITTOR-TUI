"""Standard geohash base32 encode/decode, matching bitchat's Geohash.swift.

Alphabet excludes a, i, l, o (geohash.org convention).
"""

_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"
_ALPHABET_INDEX = {c: i for i, c in enumerate(_ALPHABET)}

MIN_PRECISION = 1
MAX_PRECISION = 12


def is_valid(geohash: str) -> bool:
    if not (MIN_PRECISION <= len(geohash) <= MAX_PRECISION):
        return False
    return all(c in _ALPHABET_INDEX for c in geohash.lower())


def encode(lat: float, lon: float, precision: int = 7) -> str:
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    geohash = []
    bits = 0
    bit = 0
    even = True
    while len(geohash) < precision:
        if even:
            mid = (lon_range[0] + lon_range[1]) / 2
            if lon >= mid:
                bits = (bits << 1) | 1
                lon_range[0] = mid
            else:
                bits = bits << 1
                lon_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat >= mid:
                bits = (bits << 1) | 1
                lat_range[0] = mid
            else:
                bits = bits << 1
                lat_range[1] = mid
        even = not even
        bit += 1
        if bit == 5:
            geohash.append(_ALPHABET[bits])
            bits = 0
            bit = 0
    return "".join(geohash)


def decode_center(geohash: str) -> tuple[float, float]:
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    even = True
    for c in geohash.lower():
        idx = _ALPHABET_INDEX[c]
        for shift in range(4, -1, -1):
            bit = (idx >> shift) & 1
            if even:
                mid = (lon_range[0] + lon_range[1]) / 2
                if bit:
                    lon_range[0] = mid
                else:
                    lon_range[1] = mid
            else:
                mid = (lat_range[0] + lat_range[1]) / 2
                if bit:
                    lat_range[0] = mid
                else:
                    lat_range[1] = mid
            even = not even
    lat = (lat_range[0] + lat_range[1]) / 2
    lon = (lon_range[0] + lon_range[1]) / 2
    return lat, lon
