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

#define PARTITION_MAGIC      0x52554E31UL /* "RUN1" */
#define HASH_MK_WORDS         8U           /* SHA-256 digest, 32 bytes */

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t hash_mk[HASH_MK_WORDS];
    uint32_t crc32;    /* CRC32 over {magic,version,hash_mk} + enc_table  */
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
 * Decrypts the active partition's table into *out_table using input_mk.
 * Returns false if there is no active partition.
 */
bool Partition_Store_Load(const uint8_t *input_mk, uint32_t mk_len,
                           pwd_table_t *out_table);

/**
 * Encrypts *table with input_mk and writes header+table to the *inactive*
 * sector with version = active_version + 1 (or 1 if there is no active
 * partition yet), erasing that sector first. Does not touch the currently
 * active sector, so a power loss mid-write leaves the old data intact.
 */
bool Partition_Store_Commit(const uint8_t *input_mk, uint32_t mk_len,
                             const uint32_t hash_mk[HASH_MK_WORDS],
                             const pwd_table_t *table);

#endif /* PARTITION_STORE_H */
