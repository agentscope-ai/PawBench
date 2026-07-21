import struct
import sys
from itertools import cycle

# Define the compression table (reversed from the decoder)
_T = {
    0b000000: b'\x03',
    0b000001: b'\x00',
    0b000010: b'\x20',
    0b000011: b'\x03',
    0b000100: b'\x01',
    0b000101: b'\x65',
    0b000110: b'\x04',
    0b000111: b'\x05',
    0b001000: b'\x61',
    0b001001: b'\x04',
    0b001010: b'\x07',
    0b001011: b'\x69',
    0b001100: b'\x04',
    0b001101: b'\x06',
    0b001110: b'\x6f',
    0b001111: b'\x04',
    0b010000: b'\x04',
    0b010001: b'\x04',
    0b010010: b'\x74',
    0b010011: b'\x05',
    0b010100: b'\x16',
    0b010101: b'\x63',
    0b010110: b'\x05',
    0b010111: b'\x15',
    0b011000: b'\x64',
    0b011001: b'\x05',
    0b011010: b'\x13',
    0b011011: b'\x68',
    0b011100: b'\x05',
    0b011101: b'\x14',
    0b011110: b'\x6c',
    0b011111: b'\x05',
    0b100000: b'\x10',
    0b100001: b'\x6e',
    0b100010: b'\x05',
    0b100011: b'\x12',
    0b100100: b'\x72',
    0b100101: b'\x05',
    0b100110: b'\x11',
    0b100111: b'\x73',
    0b101000: b'\x05',
    0b101001: b'\x17',
    0b101010: b'\x75',
    0b101011: b'\x06',
    0b101100: b'\x3b',
    0b101101: b'\x0a',
    0b101110: b'\x06',
    0b101111: b'\x39',
    0b110000: b'\x2c',
    0b110001: b'\x06',
    0b110010: b'\x3a',
    0b110011: b'\x2e',
    0b110100: b'\x06',
    0b110101: b'\x36',
    0b110110: b'\x62',
    0b110111: b'\x06',
    0b111000: b'\x32',
    0b111001: b'\x66',
    0b111010: b'\x06',
    0b111011: b'\x33',
    0b111100: b'\x67',
    0b111101: b'\x06',
    0b111110: b'\x3d',
    0b111111: b'\x6a',
}

# Reverse the byte reordering
def _reverse_w(d):
    r = bytearray(len(d))
    B = 16
    for o in range(0, len(d), B):
        c = d[o:o + B]
        n = len(c)
        h = (n + 1) // 2
        for i in range(h):
            r[o + i * 2] = c[i]
        for i in range(h, n):
            r[o + (i - h) * 2 + 1] = c[i]
    return bytes(r)

# Keyed XOR Encryption
_E = (1,) * 6
def _x(d, k):
    return bytes(b ^ ((k * (i + 1) + 165) & 255) for i, b in enumerate(d))

# Bitwise Compression
def _p(s, n):
    # Convert string to a bitstream
    bitstream = ''.join(format(ord(c), '08b') for c in s)
    print(f'Bitstream: {bitstream}')  # Debug print
    q = bytearray()
    bp = 0
    while bp < len(bitstream):
        c = tuple(int(bit) for bit in bitstream[bp:bp+6])
        if c in _T:
            q.extend(_T[c])
            bp += 6
        else:
            # Handle the case where the pattern is not in the table
            q.extend(b'\x00')  # Placeholder byte
            bp += 6  # Skip 6 bits (since we are processing 6 bits at a time)
    print(f'Compressed: {q}')  # Debug print
    return bytes(q)

# Main Encode Function
def _e(s, key):
    n = len(s)
    s = _p(s, n)
    s = _reverse_w(s)
    s = _x(s, key)
    header = struct.pack('!HH', n, key)
    return header + s

if __name__ == '__main__':
    with open('fixtures/target.txt', 'r') as f:
        target_data = f.read()
    encoded_data = _e(target_data, 12345)  # Using a fixed key for simplicity
    with open('output/encoded.dat', 'wb') as f:
        f.write(encoded_data)
