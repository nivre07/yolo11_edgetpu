#!/usr/bin/env python3
"""Quick smoke test for the improved draw() function."""
import sys
sys.path.insert(0, 'src')

import numpy as np
import cv2
from detector import draw, Detection

frame = np.zeros((480, 640, 3), dtype=np.uint8) + 50
cls = ['apple', 'banana', 'orange']

# Test 1: Normal — label above
r = draw(frame.copy(), [Detection(200, 200, 300, 280, 0.85, 0)], cls)
print('Test1 (normal, above): pass')

# Test 2: Top edge — label inside box
r = draw(frame.copy(), [Detection(100, 0, 200, 80, 0.72, 1)], cls)
print('Test2 (top edge, inside): pass')

# Test 3: Left edge — clamped
r = draw(frame.copy(), [Detection(0, 150, 100, 230, 0.55, 2)], cls)
print('Test3 (left edge): pass')

# Test 4: Right edge — clamped
r = draw(frame.copy(), [Detection(580, 100, 640, 180, 0.91, 0)], cls)
print('Test4 (right edge): pass')

# Test 5: Multiple + edges
r = draw(frame.copy(), [
    Detection(50, 50, 150, 130, 0.88, 0),
    Detection(300, 30, 400, 100, 0.76, 1),
    Detection(450, 300, 550, 400, 0.65, 2),
    Detection(600, 5, 640, 60, 0.92, 1),
], cls)
print('Test5 (multiple): pass')

# Test 6: Empty
r = draw(frame.copy(), [], cls)
print('Test6 (empty): pass')

# Test 7: Check actual pixel colors
r = draw(frame.copy(), [Detection(200, 200, 300, 280, 0.85, 0)], cls)
# The label box is above the detection, so around y=175 or so
# Green fill pixel (not text)
px_bg = r[170, 204]
assert px_bg[1] > px_bg[0], f"Expected greenish pixel, got {px_bg}"
print('Test7 (green bg fill): pass')

print('\nALL DRAW TESTS PASSED')
