#define _GNU_SOURCE
#include "agterm_linux_posix.h"
#include <fcntl.h>
#include <unistd.h>

int agterm_pipe_cloexec(int fds[2])
{
    return pipe2(fds, O_CLOEXEC);
}
