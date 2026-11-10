/* Tessera Deck simulator: the firmware core running on a PC.
 *
 * Two TCP ports on 127.0.0.1:
 *   --port (default 47800)  the device's host link, speaking TDP exactly as the
 *                           USB serial port of real hardware does
 *   --ctl  (default 47801)  a line-based control channel that plays the role of
 *                           fingers and test equipment (see docs/simulator.md)
 *
 * SPDX-License-Identifier: MIT */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "td_device.h"
#include "td_fb.h"

static const td_layout *layout = &td_layout_rev_a;
static td_device dev;
static td_fb fb;
static uint16_t *master, *scan;
static uint8_t *rxbuf;
static size_t rxcap;

static int host_fd = -1, ctl_fd = -1;
static int virtual_time;
static uint32_t vclock_ms;
static const char *state_dir = "sim-state";
static int bl_on;
static uint8_t bl_pct;
static int reboot_pending = -1, reboot_count, reboot_last_mode;
static uint32_t key_raw;
static uint8_t btn_raw;
static int touch_is_down;

static uint32_t real_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000u + ts.tv_nsec / 1000000u);
}

/* ---- port -------------------------------------------------------------- */

static void p_blit(void *c, int x, int y, int w, int h, const uint16_t *px) { (void)c; td_fb_blit(&fb, x, y, w, h, px); }
static void p_fill(void *c, int x, int y, int w, int h, uint16_t col) { (void)c; td_fb_fill(&fb, x, y, w, h, col); }
static void p_backlight(void *c, bool on, uint8_t pct)
{
    (void)c;
    bl_on = on;
    bl_pct = pct;
    td_fb_set_level(&fb, pct);
}
static void p_tx(void *c, const uint8_t *data, size_t len)
{
    (void)c;
    while (host_fd >= 0 && len) {
        ssize_t n = send(host_fd, data, len, MSG_NOSIGNAL);
        if (n <= 0) {
            if (n < 0 && (errno == EINTR || errno == EAGAIN)) {
                continue;
            }
            break;
        }
        data += n;
        len -= (size_t)n;
    }
}
static uint32_t p_millis(void *c) { (void)c; return virtual_time ? vclock_ms : real_ms(); }

static void state_path(char *out, size_t n, const char *name) { snprintf(out, n, "%s/%s", state_dir, name); }

static bool read_file(const char *name, void *data, size_t len)
{
    char path[512];
    state_path(path, sizeof(path), name);
    FILE *f = fopen(path, "rb");
    if (!f) {
        return false;
    }
    bool ok = fread(data, 1, len, f) == len;
    fclose(f);
    return ok;
}
static bool write_file(const char *name, const void *data, size_t len)
{
    char path[512];
    mkdir(state_dir, 0755);
    state_path(path, sizeof(path), name);
    FILE *f = fopen(path, "wb");
    if (!f) {
        return false;
    }
    bool ok = fwrite(data, 1, len, f) == len;
    return fclose(f) == 0 && ok;
}
static bool p_nv_load(void *c, uint8_t *d, size_t n) { (void)c; return read_file("config.bin", d, n); }
static bool p_nv_save(void *c, const uint8_t *d, size_t n) { (void)c; return write_file("config.bin", d, n); }
static size_t fb_bytes(void) { return (size_t)layout->panel_w * layout->panel_h * 2; }
static bool p_splash_save(void *c) { (void)c; return write_file("splash.bin", master, fb_bytes()); }
static bool p_splash_load(void *c)
{
    (void)c;
    uint16_t *tmp = malloc(fb_bytes());
    bool ok = tmp && read_file("splash.bin", tmp, fb_bytes());
    if (ok) {
        td_fb_blit(&fb, 0, 0, layout->panel_w, layout->panel_h, tmp);
    }
    free(tmp);
    return ok;
}
static bool p_splash_clear(void *c)
{
    (void)c;
    char path[512];
    state_path(path, sizeof(path), "splash.bin");
    return unlink(path) == 0 || errno == ENOENT;
}
static void p_reboot(void *c, uint8_t mode) { (void)c; reboot_pending = mode; }
static void p_serial(void *c, char out[16]) { (void)c; memcpy(out, "SIM0000000000001", 16); }

static void device_start(void)
{
    td_port port = {
        .blit = p_blit, .fill = p_fill, .set_backlight = p_backlight, .tx = p_tx, .millis = p_millis,
        .nv_load = p_nv_load, .nv_save = p_nv_save,
        .splash_save = p_splash_save, .splash_load = p_splash_load, .splash_clear = p_splash_clear,
        .reboot = p_reboot, .serial = p_serial,
        .info_flags = TD_INFO_FLAG_TOUCH | TD_INFO_FLAG_SPLASH_STORE,
    };
    td_fb_init(&fb, master, scan, layout->panel_w, layout->panel_h, true);
    td_dev_init(&dev, layout, &port, rxbuf, rxcap);
    td_dev_boot(&dev);
    key_raw = 0;
    btn_raw = 0;
    touch_is_down = 0;
    for (unsigned i = 0; i < layout->encoders; i++) {
        td_dev_feed_enc_ab(&dev, i, true, true);
    }
}

/* ---- control channel --------------------------------------------------- */

static void ctl_reply(const char *s)
{
    if (ctl_fd >= 0) {
        (void)!write(ctl_fd, s, strlen(s));
        (void)!write(ctl_fd, "\n", 1);
    }
}

static void advance(uint32_t ms)
{
    for (uint32_t i = 0; i < ms; i++) {
        vclock_ms++;
        td_dev_tick(&dev);
    }
}

static bool screenshot(const char *path)
{
    FILE *f = fopen(path, "wb");
    if (!f) {
        return false;
    }
    fprintf(f, "P6\n%d %d\n255\n", fb.w, fb.h);
    for (int i = 0; i < fb.w * fb.h; i++) {
        uint16_t c = scan[i];
        uint8_t r = (uint8_t)(c >> 11), g = (c >> 5) & 63, b = c & 31;
        uint8_t px[3] = {(uint8_t)((r << 3) | (r >> 2)), (uint8_t)((g << 2) | (g >> 4)), (uint8_t)((b << 3) | (b >> 2))};
        fwrite(px, 1, 3, f);
    }
    return fclose(f) == 0;
}

static void ctl_line(char *line)
{
    char a[32] = "", b[400] = "";
    int n1 = 0, n2 = 0;
    char reply[96];

    if (sscanf(line, "key %d %31s", &n1, a) == 2 && n1 >= 0 && n1 < layout->rows * layout->cols) {
        key_raw = strcmp(a, "down") == 0 ? (key_raw | (1u << n1)) : (key_raw & ~(1u << n1));
        td_dev_feed_keys(&dev, key_raw);
        ctl_reply("ok");
    } else if (sscanf(line, "encbtn %d %31s", &n1, a) == 2 && n1 >= 0 && n1 < layout->encoders) {
        btn_raw = strcmp(a, "down") == 0 ? (uint8_t)(btn_raw | (1u << n1)) : (uint8_t)(btn_raw & ~(1u << n1));
        td_dev_feed_enc_buttons(&dev, btn_raw);
        ctl_reply("ok");
    } else if (sscanf(line, "enc %d %d", &n1, &n2) == 2 && n1 >= 0 && n1 < layout->encoders) {
        /* one detent = four quadrature transitions; clockwise is 11,10,00,01,11 */
        static const uint8_t cw[4] = {2, 0, 1, 3};
        static const uint8_t ccw[4] = {1, 0, 2, 3};
        const uint8_t *seq = n2 >= 0 ? cw : ccw;
        for (int d = abs(n2); d > 0; d--) {
            for (int i = 0; i < 4; i++) {
                td_dev_feed_enc_ab(&dev, (unsigned)n1, seq[i] & 2, seq[i] & 1);
            }
        }
        ctl_reply("ok");
    } else if (sscanf(line, "touch %31s %d %d", a, &n1, &n2) >= 1 &&
               (!strcmp(a, "down") || !strcmp(a, "move") || !strcmp(a, "up"))) {
        if (strcmp(a, "up") == 0) {
            touch_is_down = 0;
            td_dev_feed_touch(&dev, false, 0, 0);
        } else {
            touch_is_down = 1;
            td_dev_feed_touch(&dev, true, n1, n2);
        }
        ctl_reply("ok");
    } else if (sscanf(line, "advance %d", &n1) == 1 && n1 >= 0) {
        if (!virtual_time) {
            ctl_reply("err advance needs --virtual-time");
        } else {
            advance((uint32_t)n1);
            ctl_reply("ok");
        }
    } else if (sscanf(line, "screenshot %399s", b) == 1) {
        ctl_reply(screenshot(b) ? "ok" : "err cannot write file");
    } else if (strcmp(line, "backlight") == 0) {
        snprintf(reply, sizeof(reply), "ok %s %u", bl_on ? "on" : "off", bl_pct);
        ctl_reply(reply);
    } else if (sscanf(line, "suspend %31s", a) == 1) {
        td_dev_host_suspend(&dev, strcmp(a, "on") == 0);
        ctl_reply("ok");
    } else if (strcmp(line, "reboots") == 0) {
        snprintf(reply, sizeof(reply), "ok %d %d", reboot_count, reboot_last_mode);
        ctl_reply(reply);
    } else if (strcmp(line, "quit") == 0) {
        ctl_reply("ok");
        exit(0);
    } else {
        ctl_reply("err unknown command");
    }
}

/* ---- sockets ----------------------------------------------------------- */

static int listen_on(int port)
{
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port)};
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (fd < 0 || bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0 || listen(fd, 1) < 0) {
        perror("listen");
        exit(1);
    }
    return fd;
}

static int accept_one(int lfd)
{
    int fd = accept(lfd, NULL, NULL);
    if (fd >= 0) {
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    }
    return fd;
}

int main(int argc, char **argv)
{
    int port = 47800, ctl_port = 47801;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--port") && i + 1 < argc) {
            port = atoi(argv[++i]);
        } else if (!strcmp(argv[i], "--ctl") && i + 1 < argc) {
            ctl_port = atoi(argv[++i]);
        } else if (!strcmp(argv[i], "--state") && i + 1 < argc) {
            state_dir = argv[++i];
        } else if (!strcmp(argv[i], "--virtual-time")) {
            virtual_time = 1;
        } else {
            fprintf(stderr, "usage: %s [--port N] [--ctl N] [--state DIR] [--virtual-time]\n", argv[0]);
            return 2;
        }
    }
    signal(SIGPIPE, SIG_IGN);

    master = calloc(1, fb_bytes());
    scan = calloc(1, fb_bytes());
    rxcap = td_dev_rx_buffer_size(layout);
    rxbuf = malloc(rxcap);
    if (!master || !scan || !rxbuf) {
        return 1;
    }
    device_start();

    int lhost = listen_on(port), lctl = listen_on(ctl_port);
    printf("tessera-sim: host tcp://127.0.0.1:%d  control tcp://127.0.0.1:%d%s\n", port, ctl_port,
           virtual_time ? "  (virtual time)" : "");
    fflush(stdout);

    static char ctl_buf[1024];
    size_t ctl_have = 0;
    static uint8_t buf[65536];

    for (;;) {
        struct pollfd fds[4] = {
            {.fd = lhost, .events = POLLIN}, {.fd = lctl, .events = POLLIN},
            {.fd = host_fd, .events = POLLIN}, {.fd = ctl_fd, .events = POLLIN},
        };
        poll(fds, 4, virtual_time ? 50 : 1);

        if (fds[0].revents & POLLIN) {
            int fd = accept_one(lhost);
            if (host_fd >= 0) {
                close(fd); /* one host at a time, like a serial port */
            } else {
                host_fd = fd;
                td_dev_host_link(&dev, true);
            }
        }
        if (fds[1].revents & POLLIN) {
            int fd = accept_one(lctl);
            if (ctl_fd >= 0) {
                close(ctl_fd);
            }
            ctl_fd = fd;
            ctl_have = 0;
        }
        if (host_fd >= 0 && (fds[2].revents & (POLLIN | POLLHUP | POLLERR))) {
            ssize_t n = recv(host_fd, buf, sizeof(buf), 0);
            if (n <= 0) {
                close(host_fd);
                host_fd = -1;
                td_dev_host_link(&dev, false);
            } else {
                td_dev_rx(&dev, buf, (size_t)n);
            }
        }
        if (ctl_fd >= 0 && (fds[3].revents & (POLLIN | POLLHUP | POLLERR))) {
            ssize_t n = recv(ctl_fd, ctl_buf + ctl_have, sizeof(ctl_buf) - 1 - ctl_have, 0);
            if (n <= 0) {
                close(ctl_fd);
                ctl_fd = -1;
            } else {
                ctl_have += (size_t)n;
                ctl_buf[ctl_have] = 0;
                char *start = ctl_buf, *nl;
                while ((nl = strchr(start, '\n'))) {
                    *nl = 0;
                    if (nl > start && nl[-1] == '\r') {
                        nl[-1] = 0;
                    }
                    ctl_line(start);
                    start = nl + 1;
                }
                ctl_have = strlen(start);
                memmove(ctl_buf, start, ctl_have + 1);
                if (ctl_have >= sizeof(ctl_buf) - 1) {
                    ctl_have = 0;
                }
            }
        }
        if (!virtual_time) {
            td_dev_tick(&dev);
        }
        if (reboot_pending >= 0) {
            reboot_last_mode = reboot_pending;
            reboot_pending = -1;
            reboot_count++;
            device_start();
        }
    }
}
