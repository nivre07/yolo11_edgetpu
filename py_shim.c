/* Python 3.13 compatibility shim — pure asm approach */
#include <Python.h>

/* Force export with asm label */
__attribute__((used, visibility("default")))
PyThreadState* _PyThreadState_UncheckedGet(void);

__asm__(
    ".section .text._PyThreadState_UncheckedGet,\"ax\",@progbits\n"
    ".globl _PyThreadState_UncheckedGet\n"
    ".type _PyThreadState_UncheckedGet, @function\n"
    "_PyThreadState_UncheckedGet:\n"
    "    b PyThreadState_GetUnchecked\n"
    ".size _PyThreadState_UncheckedGet, .-_PyThreadState_UncheckedGet\n"
);
