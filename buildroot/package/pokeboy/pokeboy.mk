################################################################################
# pokeboy: emulator + battle AI from the repository checkout (local source)
################################################################################
POKEBOY_VERSION = local
POKEBOY_SITE = $(BR2_EXTERNAL_POKEBOY_PATH)/..
POKEBOY_SITE_METHOD = local
POKEBOY_SUBDIR = gameboy
POKEBOY_DEPENDENCIES = tinyalsa
POKEBOY_LICENSE = see repository
POKEBOY_CONF_OPTS = -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF -DPOKEBOY_LINUX_FRONTEND=ON -DPKAI_BUILD_ROM=OFF -DCMAKE_BUILD_TYPE=Release
# Exclude everything the target build does not need (datasets, node_modules, build dirs).
POKEBOY_EXCLUDES = ai/datasets ai/vendor ai/.venv ai/checkpoints app/node_modules build build-ai .claude notes pred-patch/*.o

define POKEBOY_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/gameboy/gbemu_linux $(TARGET_DIR)/usr/bin/pokeboy
endef

$(eval $(cmake-package))
