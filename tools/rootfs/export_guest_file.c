/* Export one bounded file from a restore guest without host-mounting System.
 * Hex records have offsets and an end-to-end CRC; the host additionally
 * verifies the original Mach-O signature before using a recovered executable.
 */
#include <stdint.h>
#include <stdio.h>
#include <sys/stat.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    FILE *input = fopen(argv[1], "rb");
    struct stat st;
    if (!input || fstat(fileno(input), &st) || !S_ISREG(st.st_mode) ||
        st.st_size <= 0 || st.st_size > 16 * 1024 * 1024) return 3;
    printf("DVM_FILE_BEGIN %lld\n", (long long)st.st_size);
    uint8_t bytes[256];
    uint32_t crc = UINT32_MAX;
    size_t offset = 0, count;
    while ((count = fread(bytes, 1, sizeof(bytes), input))) {
        printf("DVM_FILE_HEX %zx ", offset);
        for (size_t i = 0; i < count; i++) {
            printf("%02x", bytes[i]);
            crc ^= bytes[i];
            for (unsigned bit = 0; bit < 8; bit++)
                crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & (0u - (crc & 1)));
        }
        putchar('\n');
        offset += count;
    }
    if (ferror(input) || offset != (size_t)st.st_size || fclose(input)) return 4;
    printf("DVM_FILE_END %zu %08x\n", offset, crc ^ UINT32_MAX);
    if (fflush(stdout)) return 5;
    return 0;
}
