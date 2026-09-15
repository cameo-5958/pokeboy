include $(sort $(wildcard $(BR2_EXTERNAL_POKEBOY_PATH)/package/*/*.mk))

# tinyalsa 2.0.0's meson install omits attributes.h although pcm.h includes it.
define POKEBOY_TINYALSA_INSTALL_ATTRIBUTES
	$(INSTALL) -D -m 0644 $(TINYALSA_DIR)/include/tinyalsa/attributes.h \
		$(STAGING_DIR)/usr/include/tinyalsa/attributes.h
endef
TINYALSA_POST_INSTALL_STAGING_HOOKS += POKEBOY_TINYALSA_INSTALL_ATTRIBUTES
