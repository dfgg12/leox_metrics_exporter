include $(TOPDIR)/rules.mk

LUCI_TITLE:=LuCI interface for LeoX GPON ONT metrics
LUCI_DEPENDS:=+luci-base +python3

PKG_NAME:=luci-app-leoxgpon
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT

include $(TOPDIR)/feeds/luci/luci.mk

define Package/luci-app-leoxgpon/install
	$(call LuciInstall/Default,$(1))
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) ./root/usr/bin/leoxgpon $(1)/usr/bin/leoxgpon
	$(INSTALL_DIR) $(1)/usr/lib/leoxgpon
	$(INSTALL_DATA) ../scraper.py $(1)/usr/lib/leoxgpon/scraper.py
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./root/etc/init.d/leoxgpon $(1)/etc/init.d/leoxgpon
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) ./root/etc/config/leoxgpon $(1)/etc/config/leoxgpon
endef

$(eval $(call BuildPackage,luci-app-leoxgpon))
