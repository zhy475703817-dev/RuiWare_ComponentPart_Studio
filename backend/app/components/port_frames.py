"""Rigid transforms used only for read-only external-interface trials (mm, XYZ radians)."""
import math


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def transform(position, rotation):
    x, y, z = rotation
    a, b, c, d, e, f = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return [[c*e, -c*f, d, position[0]], [a*f+b*d*e, a*e-b*d*f, -b*c, position[1]],
            [b*f-a*d*e, b*e+a*d*f, a*c, position[2]], [0, 0, 0, 1]]


def inverse(m):
    return [[m[j][i] for j in range(3)] + [-sum(m[k][i]*m[k][3] for k in range(3))] for i in range(3)] + [[0, 0, 0, 1]]


def decompose(m):
    y = math.asin(max(-1, min(1, m[0][2])))
    x, z = (math.atan2(-m[1][2], m[2][2]), math.atan2(-m[0][1], m[0][0])) if abs(math.cos(y)) > 1e-8 else (math.atan2(m[2][1], m[1][1]), 0)
    return [m[i][3] for i in range(3)], [x, y, z]
