# Image Notes

This directory contains notes about test images that exposed specific quirks
or implementation details during instar development.

Each file documents:
- What quirks the image exposed
- The specific values that revealed the behavior
- Links to relevant documentation

These notes help future developers understand why certain compatibility
behaviors exist and which images can be used to verify them.

## Index

| Image | Quirks Discovered |
|-------|-------------------|
| [qcow2-v2](/components/instar/image_notes/qcow2-v2/) | L1 table file length, block rounding, banker's rounding |
| [cirros-qcow2](/components/instar/image_notes/cirros-qcow2/) | Decimal rounding, max(actual, calculated) file length |
| [virtualpc-vhd](/components/instar/image_notes/virtualpc-vhd/) | CHS-based virtual size, banker's rounding |
