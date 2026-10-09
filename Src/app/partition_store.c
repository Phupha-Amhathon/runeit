#include <string.h>
#include <stddef.h>
#include "partition_store.h"
#include "flash_drv.h"
#include "crc_drv.h"
#include "aes_ctr.h"
#include "hmac_sha256.h"
#include "kdf.h"
#include "secure_zero.h"

#define CRC_CHUNK_LEN 128U

typedef struct {
    uint8_t aes[AES128_KEY_LEN];
    hmac_sha256_ctx_t mac;
} part_keys_t;

static partition_info_t s_active;
static bool s_have_active = false;

static void DeriveKeys(const uint8_t enc_key[PARTITION_KEY_LEN], part_keys_t *keys)
{
    static const uint8_t aes_label[] = "RUNEIT-aes-v1";
    static const uint8_t mac_label[] = "RUNEIT-mac-v1";
    uint8_t d[HMAC_SHA256_LEN];

    HmacSha256(enc_key, PARTITION_KEY_LEN, aes_label, sizeof(aes_label) - 1U, d);
    (void)memcpy(keys->aes, d, AES128_KEY_LEN);
    HmacSha256(enc_key, PARTITION_KEY_LEN, mac_label, sizeof(mac_label) - 1U, d);
    HmacSha256_Init(&keys->mac, d, sizeof(d));
    Secure_Zero(d, sizeof(d));
}

/* HMAC over prefix || head || body, streamed because the three parts live in
 * different buffers. */
static void MacParts(const hmac_sha256_ctx_t *mac,
                     const uint8_t *prefix, size_t prefix_len,
                     const uint8_t *head, size_t head_len,
                     const uint8_t *body, size_t body_len,
                     uint8_t out[HMAC_SHA256_LEN])
{
    sha256_ctx_t inner = mac->inner;
    sha256_ctx_t outer = mac->outer;
    uint8_t digest[HMAC_SHA256_LEN];

    SHA256_Update(&inner, prefix, prefix_len);
    SHA256_Update(&inner, head, head_len);
    SHA256_Update(&inner, body, body_len);
    SHA256_Final(&inner, digest);
    SHA256_Update(&outer, digest, sizeof(digest));
    SHA256_Final(&outer, out);

    Secure_Zero(digest, sizeof(digest));
    Secure_Zero(&inner, sizeof(inner));
    Secure_Zero(&outer, sizeof(outer));
}

static void ComputeTag(const part_keys_t *keys, const partition_header_t *header,
                       const uint8_t *cipher, uint8_t out[HMAC_SHA256_LEN])
{
    MacParts(&keys->mac, NULL, 0U, (const uint8_t *)header,
             offsetof(partition_header_t, tag), cipher, sizeof(pwd_table_t), out);
}

static uint32_t Partition_HeaderCrcLen(void)
{
    return (uint32_t)offsetof(partition_header_t, crc32);
}

static bool ValidateCrc(uint32_t addr, const partition_header_t *header)
{
    uint8_t chunk[CRC_CHUNK_LEN];
    uint32_t table_addr = addr + (uint32_t)sizeof(partition_header_t);
    uint32_t table_len = (uint32_t)sizeof(pwd_table_t);
    uint32_t offset;

    CRC_Drv_Reset();
    CRC_Drv_Feed((const uint8_t *)header, Partition_HeaderCrcLen());

    for (offset = 0U; offset < table_len; offset += CRC_CHUNK_LEN) {
        uint32_t n;

        if ((table_len - offset) < CRC_CHUNK_LEN) {
            n = table_len - offset;
        } else {
            n = CRC_CHUNK_LEN;
        }
        Flash_Drv_Read(table_addr + offset, chunk, n);
        CRC_Drv_Feed(chunk, n);
    }

    return CRC_Drv_Result() == header->crc32;
}

static bool ReadSlot(uint32_t sector, uint32_t addr, partition_info_t *info)
{
    Flash_Drv_Read(addr, (uint8_t *)&info->header, sizeof(info->header));
    info->sector = sector;
    info->addr = addr;
    info->valid = (info->header.magic == PARTITION_MAGIC) && ValidateCrc(addr, &info->header);
    return info->valid;
}

void Partition_Store_Init(void)
{
    partition_info_t slot_a;
    partition_info_t slot_b;
    bool a_valid = ReadSlot(PARTITION_A_SECTOR, PARTITION_A_ADDR, &slot_a);
    bool b_valid = ReadSlot(PARTITION_B_SECTOR, PARTITION_B_ADDR, &slot_b);

    s_have_active = false;
    if (a_valid && (!b_valid || (slot_a.header.version >= slot_b.header.version))) {
        s_active = slot_a;
        s_have_active = true;
    } else if (b_valid) {
        s_active = slot_b;
        s_have_active = true;
    } else {
        /* neither partition is valid -> caller must Commit() a fresh one */
    }
}

const partition_info_t *Partition_Store_Active(void)
{
    const partition_info_t *active = NULL;

    if (s_have_active) {
        active = &s_active;
    } else {
        /* No action */
    }
    return active;
}

bool Partition_Store_Load(const uint8_t enc_key[PARTITION_KEY_LEN], pwd_table_t *out_table)
{
    part_keys_t keys;
    uint8_t tag[HMAC_SHA256_LEN];
    bool ok;

    if (!s_have_active) {
        return false;
    } else {
        /* No action */
    }

    DeriveKeys(enc_key, &keys);
    Flash_Drv_Read(s_active.addr + (uint32_t)sizeof(partition_header_t),
                   (uint8_t *)out_table, sizeof(*out_table));
    ComputeTag(&keys, &s_active.header, (const uint8_t *)out_table, tag);

    ok = Kdf_ConstTimeEqual(tag, s_active.header.tag, sizeof(tag));
    if (ok) {
        AesCtr_Apply((uint8_t *)out_table, sizeof(*out_table), keys.aes, s_active.header.iv);
    } else {
        Secure_Zero(out_table, sizeof(*out_table));
    }

    Secure_Zero(&keys, sizeof(keys));
    Secure_Zero(tag, sizeof(tag));
    return ok;
}

static const uint8_t s_iv_label[] = "RUNEIT-iv-v1";
static uint8_t s_commit_scratch[sizeof(pwd_table_t)];

bool Partition_Store_Commit(const uint8_t enc_key[PARTITION_KEY_LEN],
                            const uint8_t salt[PARTITION_SALT_LEN],
                            uint32_t kdf_iter,
                            const uint8_t auth[PARTITION_KEY_LEN],
                            const pwd_table_t *table)
{
    uint32_t target_sector = PARTITION_A_SECTOR;
    uint32_t target_addr = PARTITION_A_ADDR;
    uint32_t new_version = 1U;
    partition_header_t header;
    partition_info_t written;
    part_keys_t keys;
    uint8_t digest[HMAC_SHA256_LEN];
    bool ok;

    if (s_have_active) {
        if (s_active.sector == PARTITION_A_SECTOR) {
            target_sector = PARTITION_B_SECTOR;
            target_addr   = PARTITION_B_ADDR;
        } else {
            target_sector = PARTITION_A_SECTOR;
            target_addr   = PARTITION_A_ADDR;
        }
        new_version   = s_active.header.version + 1U;
    } else {
        /* No action */
    }

    DeriveKeys(enc_key, &keys);
    (void)memcpy(s_commit_scratch, table, sizeof(s_commit_scratch));

    (void)memset(&header, 0, sizeof(header));
    header.magic = PARTITION_MAGIC;
    header.version = new_version;
    header.kdf_iter = kdf_iter;
    (void)memcpy(header.salt, salt, sizeof(header.salt));
    (void)memcpy(header.auth, auth, sizeof(header.auth));

    /* Synthetic IV: a MAC of the header fields and the plaintext. It is unique
     * for every distinct (version, table) pair without needing a random
     * source, and a retried commit of identical data reuses an identical
     * keystream, which reveals nothing new. */
    MacParts(&keys.mac, s_iv_label, sizeof(s_iv_label) - 1U,
             (const uint8_t *)&header, offsetof(partition_header_t, iv),
             s_commit_scratch, sizeof(s_commit_scratch), digest);
    (void)memcpy(header.iv, digest, sizeof(header.iv));

    AesCtr_Apply(s_commit_scratch, sizeof(s_commit_scratch), keys.aes, header.iv);
    ComputeTag(&keys, &header, s_commit_scratch, header.tag);

    CRC_Drv_Reset();
    CRC_Drv_Feed((const uint8_t *)&header, Partition_HeaderCrcLen());
    CRC_Drv_Feed(s_commit_scratch, sizeof(s_commit_scratch));
    header.crc32 = CRC_Drv_Result();

    Flash_Drv_EraseSector((uint8_t)target_sector);
    Flash_Drv_Write(target_addr, (const uint8_t *)&header, sizeof(header));
    Flash_Drv_Write(target_addr + (uint32_t)sizeof(header), s_commit_scratch, sizeof(s_commit_scratch));

    Secure_Zero(s_commit_scratch, sizeof(s_commit_scratch));
    Secure_Zero(&keys, sizeof(keys));
    Secure_Zero(digest, sizeof(digest));

    /* Trust flash, not the write calls: only switch to the new partition if
     * what is really stored validates and carries the version we wrote. */
    ok = ReadSlot(target_sector, target_addr, &written) && (written.header.version == new_version);
    if (ok) {
        s_active = written;
        s_have_active = true;
    } else {
        /* No action */
    }
    return ok;
}
