# QCOW2 Encryption System

QCOW2 supports full-disk encryption to protect data at rest. Two methods
are available: legacy AES (deprecated) and LUKS (recommended).

## Encryption Methods

| Value | Name | Status | Description |
|-------|------|--------|-------------|
| 0 | NONE | Active | No encryption |
| 1 | AES | Deprecated | Legacy AES-128-CBC |
| 2 | LUKS | Recommended | Linux Unified Key Setup |

The method is stored in header field `crypt_method` (bytes 32-35).

## Legacy AES Encryption (Deprecated)

**Warning:** Legacy AES encryption is considered insecure and exists only
for data liberation from old images. Do not use for new images.

### Characteristics

- **Algorithm:** AES-128-CBC
- **Key:** Password zero-padded to 16 bytes
- **IV:** PLAIN64 (sector number as little-endian 64-bit)
- **Sector size:** 512 bytes

### Weaknesses

1. Key derived directly from password (no KDF)
2. Predictable IVs based on sector numbers
3. No integrity protection
4. Password exposed in memory

## LUKS Encryption (Recommended)

LUKS provides robust encryption following the Linux Unified Key Setup
specification (version 1.2.1).

### LUKS Header Location

The LUKS header is stored within the qcow2 file, referenced by a header
extension:

```c
// Header extension type: 0x0537be77
typedef struct Qcow2CryptoHeaderExtension {
    uint64_t offset;   // Offset to LUKS header (cluster-aligned)
    uint64_t length;   // LUKS header length
} qemu_PACKED;
```

### LUKS Header Structure

```c
struct QCryptoBlockLUKSHeader {
    // Magic and version
    char magic[6];              // "LUKS\xba\xbe"
    uint16_t version;           // 1

    // Cipher specification
    char cipher_name[32];       // e.g., "aes"
    char cipher_mode[32];       // e.g., "xts-plain64"
    char hash_spec[32];         // e.g., "sha256"

    // Master key info
    uint32_t payload_offset_sector;  // Start of encrypted data
    uint32_t master_key_len;         // Key size in bytes
    uint8_t master_key_digest[20];   // SHA1 for verification
    uint8_t master_key_salt[32];     // PBKDF2 salt
    uint32_t master_key_iterations;  // PBKDF2 iterations

    // UUID
    uint8_t uuid[40];

    // Key slots (8 total)
    struct {
        uint32_t active;         // 0x00AC71F3=enabled, 0x0000DEAD=disabled
        uint32_t iterations;     // Slot-specific iterations
        uint8_t salt[32];        // Slot-specific salt
        uint32_t key_offset_sector;  // Encrypted key material offset
        uint32_t stripes;        // Anti-forensic stripes (default 4000)
    } key_slots[8];
} qemu_PACKED;  // Total: 592 bytes
```

### Supported Ciphers

| Cipher | Key Sizes | Notes |
|--------|-----------|-------|
| aes | 128, 192, 256 | Most common |
| twofish | 128, 192, 256 | Alternative |
| serpent | 128, 192, 256 | Alternative |
| cast5 | 128 | Legacy |
| sm4 | 128 | Chinese standard |

### Cipher Modes

| Mode | Description |
|------|-------------|
| xts-plain64 | XTS with 64-bit sector IV (recommended) |
| cbc-plain64 | CBC with 64-bit sector IV |
| cbc-essiv:sha256 | CBC with ESSIV |

## IV Generation Methods

### PLAIN64

```c
void plain64_generate_iv(uint64_t sector, uint8_t *iv, size_t iv_len) {
    memset(iv, 0, iv_len);
    *(uint64_t*)iv = cpu_to_le64(sector);
}
```

Simple but deterministic. Suitable with XTS mode.

### ESSIV (Encrypted Salt-Sector IV)

More secure IV generation using encryption:

```c
void essiv_generate_iv(uint64_t sector, uint8_t *iv, size_t iv_len,
                       const uint8_t *key, size_t key_len,
                       const char *hash_alg) {
    // 1. Hash the master key to create ESSIV key
    uint8_t essiv_key[32];
    hash(hash_alg, key, key_len, essiv_key);

    // 2. Create sector input (little-endian, padded)
    uint8_t sector_buf[16] = {0};
    *(uint64_t*)sector_buf = cpu_to_le64(sector);

    // 3. Encrypt sector number with ESSIV key
    aes_ecb_encrypt(essiv_key, sector_buf, iv);
}
```

Provides unpredictable IVs even for sequential sectors.

## Key Management

### Key Derivation (PBKDF2)

LUKS uses PBKDF2 to derive encryption keys from passwords:

```c
void derive_key(const char *password, const uint8_t *salt,
                int iterations, uint8_t *key, size_t key_len) {
    PKCS5_PBKDF2_HMAC(password, strlen(password),
                      salt, 32,
                      iterations,
                      EVP_sha256(),
                      key_len, key);
}
```

### Key Slots

LUKS supports 8 independent key slots, each allowing different passwords:

```
Slot Activation:
1. User provides password
2. Derive slot key: PBKDF2(password, slot_salt, slot_iterations)
3. Decrypt key material at slot_key_offset
4. Apply anti-forensic merge to recover master key
5. Verify: PBKDF2(master_key, master_salt) == master_digest
6. If match, master key is valid
```

### Anti-Forensic Splitting (AFsplit)

Protects master key from partial disk recovery:

```c
// Splitting (for storage)
void af_split(const uint8_t *key, size_t key_len,
              int stripes, uint8_t *split_key) {
    uint8_t *d = calloc(key_len, 1);

    for (int i = 0; i < stripes - 1; i++) {
        random_bytes(split_key + i * key_len, key_len);
        hash_with_counter(d, split_key + i * key_len, key_len, i);
    }

    // Final stripe = key XOR accumulated hash
    xor_buffers(split_key + (stripes-1) * key_len, key, d, key_len);
    free(d);
}

// Merging (for recovery)
void af_merge(const uint8_t *split_key, size_t key_len,
              int stripes, uint8_t *key) {
    uint8_t *d = calloc(key_len, 1);

    for (int i = 0; i < stripes; i++) {
        hash_with_counter(d, split_key + i * key_len, key_len, i);
    }

    memcpy(key, d, key_len);
    free(d);
}
```

## Encrypted Cluster I/O

### Read Path

```c
int read_encrypted_cluster(BDRVQcow2State *s, uint64_t host_offset,
                           uint64_t guest_offset, void *buf, size_t len) {
    // 1. Read encrypted data from disk
    pread(s->fd, encrypted_buf, len, host_offset);

    // 2. Determine IV offset (physical or virtual)
    uint64_t iv_offset = s->crypt_physical_offset ? host_offset : guest_offset;

    // 3. Decrypt in 512-byte sectors
    for (int i = 0; i < len / 512; i++) {
        uint64_t sector = (iv_offset / 512) + i;
        decrypt_sector(s->crypto, sector, encrypted_buf + i*512, buf + i*512);
    }

    return 0;
}
```

### Write Path

```c
int write_encrypted_cluster(BDRVQcow2State *s, uint64_t host_offset,
                            uint64_t guest_offset, const void *buf, size_t len) {
    // 1. Determine IV offset
    uint64_t iv_offset = s->crypt_physical_offset ? host_offset : guest_offset;

    // 2. Encrypt in 512-byte sectors
    for (int i = 0; i < len / 512; i++) {
        uint64_t sector = (iv_offset / 512) + i;
        encrypt_sector(s->crypto, sector, buf + i*512, encrypted_buf + i*512);
    }

    // 3. Write encrypted data to disk
    pwrite(s->fd, encrypted_buf, len, host_offset);

    return 0;
}
```

## Physical vs Virtual Offset for IV

Two modes exist for determining the sector number used in IV generation:

### Virtual Offset (Guest Address)

- Default for LUKS encryption
- IV based on guest-visible disk offset
- Same data at different locations has different ciphertext
- Better security model

### Physical Offset (Host Address)

- Used by legacy AES encryption
- IV based on actual file position
- Moving data changes its ciphertext
- Required for legacy compatibility

## Maximum Encrypted Clusters

```c
#define QCOW_MAX_CRYPT_CLUSTERS 32
```

I/O operations are limited to 32 clusters to bound memory usage.

## Creating Encrypted Images

```bash
# Create LUKS-encrypted qcow2 image
qemu-img create -f qcow2 \
    -o encryption=on,encrypt.format=luks,encrypt.key-secret=sec0 \
    encrypted.qcow2 10G
```

## Opening Encrypted Images

```bash
# Open with password
qemu-system-x86_64 \
    -object secret,id=sec0,data=mypassword \
    -drive file=encrypted.qcow2,encrypt.key-secret=sec0
```

## Security Considerations

1. **Use LUKS, not legacy AES**
   - LUKS provides proper key derivation and IV generation

2. **Strong passwords**
   - PBKDF2 iterations scale to hardware
   - Longer passwords resist brute force

3. **Key slot management**
   - Can have multiple passwords
   - Remove unused slots with `cryptsetup luksKillSlot`

4. **Memory security**
   - Master key kept in memory while image is open
   - Use secure memory allocation if available

5. **Backup considerations**
   - Encrypted backups still require password
   - Consider key escrow for disaster recovery

6. **XTS vs CBC**
   - XTS preferred for disk encryption
   - CBC+ESSIV acceptable but older

7. **Sector size alignment**
   - All I/O must align to 512-byte sectors
   - Unaligned access not supported
