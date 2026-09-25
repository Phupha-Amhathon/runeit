#include <string.h>
#include <stddef.h>
#include "partition_store.h"
#include "flash_drv.h"
#include "crc_drv.h"
#include "xor_cipher.h"
#include "secure_zero.h"

#define CRC_CHUNK_LEN 128U

static partition_info_t s_active;
static bool s_have_active = false;

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
        uint32_t n = ((table_len - offset) < CRC_CHUNK_LEN) ? (table_len - offset) : CRC_CHUNK_LEN;
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
    return s_have_active ? &s_active : NULL;
}

bool Partition_Store_Load(const uint8_t enc_key[PARTITION_KEY_LEN], pwd_table_t *out_table)
{
    if (!s_have_active) {
        return false;
    }

    Flash_Drv_Read(s_active.addr + (uint32_t)sizeof(partition_header_t),
                   (uint8_t *)out_table, sizeof(*out_table));
    XorCipher_Apply((uint8_t *)out_table, sizeof(*out_table),
                    enc_key, PARTITION_KEY_LEN, s_active.header.version);
    return true;
}

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
    bool ok;

    if (s_have_active) {
        bool active_is_a = (s_active.sector == PARTITION_A_SECTOR);
        target_sector = active_is_a ? PARTITION_B_SECTOR : PARTITION_A_SECTOR;
        target_addr   = active_is_a ? PARTITION_B_ADDR   : PARTITION_A_ADDR;
        new_version   = s_active.header.version + 1U;
    }

    (void)memcpy(s_commit_scratch, table, sizeof(s_commit_scratch));
    XorCipher_Apply(s_commit_scratch, sizeof(s_commit_scratch),
                    enc_key, PARTITION_KEY_LEN, new_version);

    header.magic = PARTITION_MAGIC;
    header.version = new_version;
    header.kdf_iter = kdf_iter;
    (void)memcpy(header.salt, salt, sizeof(header.salt));
    (void)memcpy(header.auth, auth, sizeof(header.auth));

    CRC_Drv_Reset();
    CRC_Drv_Feed((const uint8_t *)&header, Partition_HeaderCrcLen());
    CRC_Drv_Feed(s_commit_scratch, sizeof(s_commit_scratch));
    header.crc32 = CRC_Drv_Result();

    Flash_Drv_EraseSector((uint8_t)target_sector);
    Flash_Drv_Write(target_addr, (const uint8_t *)&header, sizeof(header));
    Flash_Drv_Write(target_addr + (uint32_t)sizeof(header), s_commit_scratch, sizeof(s_commit_scratch));

    Secure_Zero(s_commit_scratch, sizeof(s_commit_scratch));

    /* Trust flash, not the write calls: only switch to the new partition if
     * what is really stored validates and carries the version we wrote. */
    ok = ReadSlot(target_sector, target_addr, &written) && (written.header.version == new_version);
    if (ok) {
        s_active = written;
        s_have_active = true;
    }
    return ok;
}
