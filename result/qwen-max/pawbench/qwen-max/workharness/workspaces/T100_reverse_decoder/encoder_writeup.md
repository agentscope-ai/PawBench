# Encoder Writeup

The encoder script `encoder.py` is designed to produce an encoded binary file that, when decoded by `fixtures/decoder.py`, results in the exact content of `fixtures/target.txt`. The encoder performs the following steps:

1. **Bitwise Compression**: The target text is compressed into a bitstream using a predefined table `_T`.
2. **Reverse Byte Reordering**: The bytes are reordered in a specific pattern that is the reverse of the reordering done by the decoder.
3. **Keyed XOR Encryption**: A keyed XOR operation is applied to the reordered bytes using a fixed key.
4. **Header Addition**: A 4-byte header is added at the beginning of the encoded data, which includes the length of the original text and the key used for the XOR operation.

The resulting `output/encoded.dat` file should meet the size constraint of being at most 60% the size of `fixtures/target.txt`.

To use the encoder, run the following command:
```sh
python encoder.py
```
This will generate the `output/encoded.dat` file, which can then be decoded using the provided decoder to verify the result.