/**
 * @file    partition_store.h
 * @brief   A/B flash partition layout and read/select/commit primitives.
 *
 * Each partition (sector 2 = A, sector 3 = B) starts with a self-describing
 * header, followed immediately by the encrypted password table. On INIT the
 * header lets us decide which partition is authoritative without relying on
 * any RAM-only "active partition" pointer that would be lost on reset:
 * whichever partition has a valid magic + CRC, and the higher version
 * number, wins.
 *
 * The header carries the key-derivation parameters (salt, iteration count)
 * and the stored auth value. The cipher key itself is never stored: it is
 * derived from the master key by crypto/kdf and handed to Load()/Commit().
 */
#ifndef PARTITION_STORE_H
#define PARTITION_STORE_H

#include <stdint.h>
#include <stdbool.h>
#include "password_table.h"

#define PARTITION_A_ADDR     0x08008000UL
#define PARTITION_A_SECTOR   2U
#define PARTITION_B_ADDR     0x0800C000UL
#define PARTITION_B_SECTOR   3U
#define PARTITION_SECTOR_SIZE 0x4000UL /* 16 KB */

/* "RUN2": header format v2. A partition written by the Stage B firmware
 * ("RUN1", hardcoded key) is deliberately not recognised, so such a device
 * starts again at FIRST_MEET instead of trusting the old layout. */
#define PARTITION_MAGIC      0x52554E32UL

#define PARTITION_SALT_LEN   16U
#define PARTITION_KEY_LEN    32U

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t kdf_iter;
    uint8_t  salt[PARTITION_SALT_LEN];
    uint8_t  auth[PARTITION_KEY_LEN];
    uint32_t crc32;    /* CRC32 over everything before this field + the encrypted table */
} partition_header_t;

typedef struct {
    bool     valid;
    uint32_t sector;
    uint32_t addr;
    partition_header_t header;
} partition_info_t;

/** Reads both sector headers and records whichever is valid/newest. */
void Partition_Store_Init(void);

/** NULL if neither partition is valid yet (first boot -> FIRST_MEET). */
const partition_info_t *Partition_Store_Active(void);

/**
 * Decrypts the active partition's table into *out_table with the derived
 * cipher key. Returns false if there is no active partition. It cannot tell
 * a wrong key from a right one -- callers must have checked the header's
 * auth value first (see session.c).
 */
bool Partition_Store_Load(const uint8_t enc_key[PARTITION_KEY_LEN], pwd_table_t *out_table);

/**
 * Encrypts *table with enc_key and writes header+table to the *inactive*
 * sector with version = active_version + 1 (or 1 if there is no active
 * partition yet), erasing that sector first. The currently active sector is
 * never touched, so a power loss mid-write leaves the old data intact.
 *
 * The written partition is read back and validated; returns false (and keeps
 * the previous partition active) if it did not verify.
 */
bool Partition_Store_Commit(const uint8_t enc_key[PARTITION_KEY_LEN],
                            const uint8_t salt[PARTITION_SALT_LEN],
                            uint32_t kdf_iter,
                            const uint8_t auth[PARTITION_KEY_LEN],
                            const pwd_table_t *table);

#endif /* PARTITION_STORE_H */
