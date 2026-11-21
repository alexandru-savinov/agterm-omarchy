#define _GNU_SOURCE
#include <fcntl.h>
#include <linux/uinput.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <unistd.h>

static void checked(int result, const char *action) {
    if (result < 0) {
        perror(action);
        exit(EXIT_FAILURE);
    }
}

static void emit(int fd, unsigned short type, unsigned short code, int value) {
    const struct input_event event = {.type = type, .code = code, .value = value};
    if (write(fd, &event, sizeof(event)) != sizeof(event)) {
        perror("write input event");
        exit(EXIT_FAILURE);
    }
}

int main(void) {
    sigset_t signals;
    sigemptyset(&signals);
    sigaddset(&signals, SIGUSR1);
    sigaddset(&signals, SIGTERM);
    checked(sigprocmask(SIG_BLOCK, &signals, NULL), "sigprocmask");

    int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    checked(fd, "open /dev/uinput");
    printf("uinput opened uid=%lu\n", (unsigned long)getuid());
    checked(ioctl(fd, UI_SET_EVBIT, EV_KEY), "UI_SET_EVBIT");
    /* Advertise ordinary keyboard keys so udev classifies it as a keyboard. */
    for (int key = KEY_ESC; key <= KEY_SPACE; key++) {
        checked(ioctl(fd, UI_SET_KEYBIT, key), "UI_SET_KEYBIT");
    }
    checked(ioctl(fd, UI_SET_KEYBIT, KEY_F13), "UI_SET_KEYBIT F13");
    struct uinput_setup keyboard = {
        .id = {.bustype = BUS_USB, .vendor = 0x1, .product = 0x1},
        .name = "Omarchy native-feel probe keyboard",
    };
    checked(ioctl(fd, UI_DEV_SETUP, &keyboard), "UI_DEV_SETUP");
    checked(ioctl(fd, UI_DEV_CREATE), "UI_DEV_CREATE");
    char sysname[64];
    checked(ioctl(fd, UI_GET_SYSNAME(sizeof(sysname)), sysname), "UI_GET_SYSNAME");
    printf("keyboard-created=%s\n", sysname);
    fflush(stdout);

    int signal;
    while (sigwait(&signals, &signal) == 0 && signal == SIGUSR1) {
        emit(fd, EV_KEY, KEY_F13, 1);
        emit(fd, EV_SYN, SYN_REPORT, 0);
        emit(fd, EV_KEY, KEY_F13, 0);
        emit(fd, EV_SYN, SYN_REPORT, 0);
        puts("uinput emitted F13 press and release");
        fflush(stdout);
    }
    checked(ioctl(fd, UI_DEV_DESTROY), "UI_DEV_DESTROY");
    return close(fd) != 0;
}
