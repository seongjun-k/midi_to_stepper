# PlatformIO 빌드 훅.
#
# 1) 플래시 오프셋 강제: platformio.ini의 board_build.offset 은 이 플랫폼/보드
#    조합에서 무시되고(-Wl,--defsym=LD_FLASH_OFFSET=0x0 이 그대로 나감),
#    build_flags에 직접 써도 PlatformIO가 뒤에 자기 값을 붙여 덮어쓴다.
#    그래서 링크 직전에 LINKFLAGS를 직접 고쳐넣는다. (Marlin도 같은 방식)
# 2) SD카드 업로드용 파일명으로 복사: MKS 부트로더는 루트의 Robin_nano35.bin을 찾는다.

import shutil

Import("env")

FLASH_OFFSET = "0x7000"   # MKS 부트로더가 0x0000~0x7000을 차지한다
SD_BIN_NAME = "Robin_nano35.bin"

env.Replace(
    LINKFLAGS=[
        "-Wl,--defsym=LD_FLASH_OFFSET=" + FLASH_OFFSET if "LD_FLASH_OFFSET" in f else f
        for f in env["LINKFLAGS"]
    ]
)


def copy_for_sd(source, target, env):
    src = str(target[0])
    dst = str(target[0].dir) + "/" + SD_BIN_NAME
    shutil.copyfile(src, dst)
    print("SD upload image: " + dst)


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", copy_for_sd)
