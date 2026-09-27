/**
 * Map Coord Picker — tw-bot
 * Filtreler: header flexible gap (köy / hammadde arası)
 * Sonuç: #map_config (sağ panel) + floating widget
 * Haritada gezinince eşleşen köyler birikir. «Bot'a aktar» → twMapCoordBridge.setCoords
 */
(function () {
    'use strict';

    if (typeof game_data === 'undefined' || !game_data || game_data.screen !== 'map') {
        window.alert('Koordinat seçici yalnızca harita (map) ekranında çalışır.');
        return;
    }
    if (typeof TWMap === 'undefined' || !TWMap || !TWMap.mapHandler) {
        window.alert('Harita (TWMap) henüz yüklenmedi; sayfayı yenileyin.');
        return;
    }

    // Önceki UI temizle
    if (window.__twMapCoordPickerLoaded) {
        jQuery('#ra-map-coord-picker, #twMapCoordFilters, #twMapCoordResultPanel').remove();
        if (TWMap.mapHandler._twSpawnSectorOrig) {
            TWMap.mapHandler.spawnSector = TWMap.mapHandler._twSpawnSectorOrig;
        }
        if (TWMap.map && TWMap.map._twHandleClickOrig) {
            TWMap.map._handleClick = TWMap.map._twHandleClickOrig;
        }
    }
    window.__twMapCoordPickerLoaded = true;

    var selectedVillages = [];
    var mapOverlay = TWMap;
    var FILTER_IDS = {
        barb: 'twMapFilterBarb',
        player: 'twMapFilterPlayer',
        clan: 'twMapFilterClan',
        playerNick: 'twMapFilterPlayerNick',
        clanTag: 'twMapFilterClanTag',
    };

    function norm(s) {
        return String(s || '')
            .trim()
            .toLowerCase()
            .replace(/\s+/g, ' ');
    }

    function coordFromVillage(v, x, y) {
        if (!v) return null;
        if (typeof x === 'number' && typeof y === 'number' && !isNaN(x) && !isNaN(y)) {
            return x + '|' + y;
        }
        if (v.xy != null && v.xy !== '') {
            var vXY = String(v.xy);
            if (vXY.length >= 6) return vXY.slice(0, 3) + '|' + vXY.slice(3, 6);
        }
        if (v.x != null && v.y != null) return v.x + '|' + v.y;
        return null;
    }

    function villageOwnerId(v) {
        if (!v) return 0;
        if (v.owner != null) return parseInt(v.owner, 10) || 0;
        if (v.player != null) return parseInt(v.player, 10) || 0;
        if (v.player_id != null) return parseInt(v.player_id, 10) || 0;
        return 0;
    }

    function villageAllyId(v, ownerId) {
        if (!v) return 0;
        if (v.ally != null && v.ally !== '') return parseInt(v.ally, 10) || 0;
        if (v.tribe != null && v.tribe !== '') return parseInt(v.tribe, 10) || 0;
        if (v.ally_id != null) return parseInt(v.ally_id, 10) || 0;
        var pid = ownerId || villageOwnerId(v);
        if (!pid) return 0;
        var p = getPlayer(pid);
        if (!p) return 0;
        if (Array.isArray(p)) return parseInt(p[2], 10) || 0;
        return parseInt(p.ally || p.ally_id || 0, 10) || 0;
    }

    function getPlayer(pid) {
        if (!pid || !TWMap.players) return null;
        return TWMap.players[pid] || TWMap.players[String(pid)] || null;
    }

    function getAlly(aid) {
        if (!aid || !TWMap.allies) return null;
        return TWMap.allies[aid] || TWMap.allies[String(aid)] || null;
    }

    function playerName(pid) {
        var p = getPlayer(pid);
        if (!p) return '';
        if (Array.isArray(p)) return String(p[0] || '');
        return String(p.name || '');
    }

    function allyTag(aid) {
        var a = getAlly(aid);
        if (!a) return '';
        if (Array.isArray(a)) return String(a[2] || a[0] || '');
        return String(a.tag || a.name || '');
    }

    function filterState() {
        var barb = !!jQuery('#' + FILTER_IDS.barb).is(':checked');
        var playerOn = !!jQuery('#' + FILTER_IDS.player).is(':checked');
        var clanOn = !!jQuery('#' + FILTER_IDS.clan).is(':checked');
        var nick = norm(jQuery('#' + FILTER_IDS.playerNick).val());
        var tag = norm(jQuery('#' + FILTER_IDS.clanTag).val());
        return {
            barb: barb,
            playerOn: playerOn,
            clanOn: clanOn,
            nick: nick,
            tag: tag,
            any: barb || (playerOn && !!nick) || (clanOn && !!tag),
        };
    }

    function villageMatchesFilters(v, fs) {
        if (!v || !fs || !fs.any) return false;
        var owner = villageOwnerId(v);
        if (fs.barb && owner === 0) return true;
        if (fs.playerOn && fs.nick && owner > 0) {
            if (norm(playerName(owner)) === fs.nick) return true;
        }
        if (fs.clanOn && fs.tag && owner > 0) {
            var aid = villageAllyId(v, owner);
            if (aid && norm(allyTag(aid)) === fs.tag) return true;
        }
        return false;
    }

    function addCoord(coord) {
        if (!coord) return false;
        if (selectedVillages.indexOf(coord) >= 0) return false;
        selectedVillages.push(coord);
        return true;
    }

    function refreshList() {
        var text = selectedVillages.join(' ');
        jQuery('#twMapCoordList').val(text);
        jQuery('#twMapCoordCount').text(selectedVillages.length);
    }

    function highlightVillage(v, on) {
        if (!v || !v.id) return;
        var el = jQuery('#map_village_' + v.id);
        if (!el.length) return;
        el.css({ filter: on ? 'brightness(200%) grayscale(100%)' : 'none' });
    }

    function scanLoadedVillages(opts) {
        opts = opts || {};
        var fs = filterState();
        if (!fs.any) {
            if (opts.resetSelection) {
                selectedVillages = [];
                refreshList();
            }
            return;
        }
        if (opts.clearFirst) {
            selectedVillages = [];
        }
        var villages = TWMap.villages || {};
        var key;
        for (key in villages) {
            if (!Object.prototype.hasOwnProperty.call(villages, key)) continue;
            var v = villages[key];
            if (!v) continue;
            var n = parseInt(key, 10);
            var x = Math.floor(n / 1000);
            var y = n % 1000;
            if (!villageMatchesFilters(v, fs)) continue;
            var coord = coordFromVillage(v, x, y);
            if (addCoord(coord)) highlightVillage(v, true);
        }
        refreshList();
    }

    function scanSectorVillages(data, sector) {
        var fs = filterState();
        if (!fs.any) return;
        var beginX = sector.x - data.x;
        var endX = beginX + mapOverlay.mapSubSectorSize;
        var beginY = sector.y - data.y;
        var endY = beginY + mapOverlay.mapSubSectorSize;
        var x, y, xCoord, yCoord, v, coord;
        for (x in data.tiles) {
            x = parseInt(x, 10);
            if (x < beginX || x >= endX) continue;
            for (y in data.tiles[x]) {
                y = parseInt(y, 10);
                if (y < beginY || y >= endY) continue;
                xCoord = data.x + x;
                yCoord = data.y + y;
                v = mapOverlay.villages[xCoord * 1000 + yCoord];
                if (!v) continue;
                coord = coordFromVillage(v, xCoord, yCoord);
                if (villageMatchesFilters(v, fs)) {
                    if (addCoord(coord)) highlightVillage(v, true);
                } else if (coord && selectedVillages.indexOf(coord) >= 0) {
                    highlightVillage(v, true);
                }
            }
        }
        refreshList();
    }

    function syncFilterInputsEnabled() {
        var pOn = jQuery('#' + FILTER_IDS.player).is(':checked');
        var cOn = jQuery('#' + FILTER_IDS.clan).is(':checked');
        jQuery('#' + FILTER_IDS.playerNick).prop('disabled', !pOn);
        jQuery('#' + FILTER_IDS.clanTag).prop('disabled', !cOn);
    }

    function onFilterChange() {
        syncFilterInputsEnabled();
        // Filtre değişince yüklü köyleri yeniden tara (liste birikir = clearFirst false;
        // pratikte eski filtre kalıntısı kalmasın diye clear + rescan)
        selectedVillages = [];
        jQuery('[id^=map_village_]').css({ filter: 'none' });
        scanLoadedVillages({ clearFirst: true });
        try {
            TWMap.reload();
        } catch (e) {}
    }

    function pushToBot() {
        var text = selectedVillages.join(' ');
        if (!text) {
            if (typeof UI !== 'undefined' && UI.ErrorMessage) {
                UI.ErrorMessage('Seçili köy yok.', 3000);
            }
            return;
        }
        if (window.twMapCoordBridge && typeof window.twMapCoordBridge.setCoords === 'function') {
            try {
                window.twMapCoordBridge.setCoords(text);
                if (typeof UI !== 'undefined' && UI.SuccessMessage) {
                    UI.SuccessMessage("Bot'a aktarıldı (Fake planı hedefleri).", 3500);
                }
                return;
            } catch (e) {
                console.warn('[map-coord-picker] bridge', e);
            }
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text);
            if (typeof UI !== 'undefined' && UI.SuccessMessage) {
                UI.SuccessMessage('Panoya kopyalandı (köprü yok).', 3000);
            }
        } else {
            jQuery('#twMapCoordList').select();
            document.execCommand('copy');
            if (typeof UI !== 'undefined' && UI.SuccessMessage) {
                UI.SuccessMessage('Kopyalandı.', 3000);
            }
        }
    }

    function findHeaderGapTd() {
        var $row = jQuery('#header_info tr').first();
        if (!$row.length) return null;
        var $tds = $row.children('td.topAlign');
        var i;
        // flexible gap: boş / yalnızca whitespace olan topAlign hücre
        for (i = 0; i < $tds.length; i++) {
            var $td = jQuery($tds[i]);
            if ($td.find('.header-border, .menu_block_right, #wood, #menu_row2').length) continue;
            var html = ($td.html() || '').replace(/\s|<!--[\s\S]*?-->/g, '');
            if (html === '') return $td;
        }
        // fallback: köy kutusundan sonraki ilk topAlign
        var $afterVillage = $row.find('#menu_row2_village').closest('td.topAlign');
        if ($afterVillage.length) {
            var $next = $afterVillage.nextAll('td.topAlign').first();
            if ($next.length) return $next;
        }
        return null;
    }

    function buildFilterBar() {
        jQuery('#twMapCoordFilters').remove();
        var html =
            '<div id="twMapCoordFilters" style="display:flex;flex-direction:column;gap:2px;font-size:11px;line-height:1.25;text-align:left;padding:2px 6px;max-width:280px;">' +
            '<label style="white-space:nowrap;cursor:pointer;">' +
            '<input type="checkbox" id="' +
            FILTER_IDS.barb +
            '"/> Barbar koordinatlarını çıkar</label>' +
            '<label style="white-space:nowrap;cursor:pointer;display:flex;align-items:center;gap:4px;">' +
            '<input type="checkbox" id="' +
            FILTER_IDS.player +
            '"/> Oyuncu koordinatlarını çıkar' +
            '<input type="text" id="' +
            FILTER_IDS.playerNick +
            '" placeholder="nickname" disabled ' +
            'style="width:90px;font-size:11px;padding:1px 3px;" /></label>' +
            '<label style="white-space:nowrap;cursor:pointer;display:flex;align-items:center;gap:4px;">' +
            '<input type="checkbox" id="' +
            FILTER_IDS.clan +
            '"/> Klan koordinatlarını çıkar' +
            '<input type="text" id="' +
            FILTER_IDS.clanTag +
            '" placeholder="kısaltma" disabled ' +
            'style="width:70px;font-size:11px;padding:1px 3px;" /></label>' +
            '</div>';

        var $gap = findHeaderGapTd();
        if ($gap && $gap.length) {
            $gap.append(html);
        } else {
            jQuery('#header_info').prepend(
                '<tr><td colspan="5" style="padding:4px;">' + html + '</td></tr>'
            );
        }

        jQuery('#' + FILTER_IDS.barb + ', #' + FILTER_IDS.player + ', #' + FILTER_IDS.clan).on(
            'change',
            onFilterChange
        );
        var nickTimer = null;
        jQuery('#' + FILTER_IDS.playerNick + ', #' + FILTER_IDS.clanTag).on('change', onFilterChange);
        jQuery('#' + FILTER_IDS.playerNick + ', #' + FILTER_IDS.clanTag).on('keyup', function () {
            if (nickTimer) clearTimeout(nickTimer);
            nickTimer = setTimeout(function () {
                var fs = filterState();
                if ((fs.playerOn && fs.nick) || (fs.clanOn && fs.tag) || fs.barb) {
                    onFilterChange();
                }
            }, 450);
        });
        syncFilterInputsEnabled();
    }

    function buildFloatingUI() {
        var id = 'ra-map-coord-picker';
        jQuery('#' + id).remove();
        var html =
            '<div class="ra-fixed-widget" id="' +
            id +
            '" style="position:fixed;top:10vw;right:10vw;z-index:99999;border:2px solid #7d510f;border-radius:10px;padding:10px;width:300px;max-height:85vh;overflow:auto;background:#e3d5b3 url(\'/graphic/index/main_bg.jpg\') top right repeat;">' +
            '<a class="popup_box_close" href="#" id="twMapCoordClose" style="position:absolute;right:6px;top:4px;">&nbsp;</a>' +
            '<h3 style="margin:0 0 8px 0;font-size:14px;">Harita — koordinat</h3>' +
            '<label style="display:block;font-weight:600;margin-bottom:4px;">Seçili: <span id="twMapCoordCount">0</span></label>' +
            '<textarea id="twMapCoordList" rows="4" style="width:100%;resize:vertical;font-family:Consolas,monospace;font-size:11px;"></textarea>' +
            '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;">' +
            '<a href="#" class="btn" id="twMapCoordReset">Sıfırla</a>' +
            '<a href="#" class="btn" id="twMapCoordCopy">Kopyala</a>' +
            '<a href="#" class="btn btn-confirm-yes" id="twMapCoordBot">Bot\'a aktar</a>' +
            '</div>' +
            '<p style="margin:8px 0 0;font-size:10px;color:#444;">Filtre açıkken haritada gezinince eşleşenler birikir. Köye tık = elle ekle/çıkar.</p>' +
            '</div>';
        jQuery('#contentContainer').prepend(html);

        jQuery('#twMapCoordClose').on('click', function (e) {
            e.preventDefault();
            teardown();
        });
        jQuery('#twMapCoordReset').on('click', function (e) {
            e.preventDefault();
            selectedVillages = [];
            jQuery('[id^=map_village_]').css({ filter: 'none' });
            refreshList();
            try {
                TWMap.reload();
            } catch (err) {}
            if (typeof UI !== 'undefined' && UI.SuccessMessage) {
                UI.SuccessMessage('Seçim temizlendi.', 2500);
            }
        });
        jQuery('#twMapCoordCopy').on('click', function (e) {
            e.preventDefault();
            var c = jQuery('#twMapCoordList').val().trim();
            if (!c) {
                if (typeof UI !== 'undefined' && UI.ErrorMessage)
                    UI.ErrorMessage('Kopyalanacak bir şey yok.', 3000);
                return;
            }
            jQuery('#twMapCoordList').select();
            document.execCommand('copy');
            if (typeof UI !== 'undefined' && UI.SuccessMessage) UI.SuccessMessage('Kopyalandı!', 3000);
        });
        jQuery('#twMapCoordBot').on('click', function (e) {
            e.preventDefault();
            pushToBot();
        });

        try {
            jQuery('#' + id).draggable({ cancel: 'textarea, input, .btn' });
        } catch (e2) {}
    }

    function teardown() {
        jQuery('#ra-map-coord-picker, #twMapCoordFilters, #twMapCoordResultPanel').remove();
        if (mapOverlay.mapHandler._twSpawnSectorOrig) {
            TWMap.mapHandler.spawnSector = mapOverlay.mapHandler._twSpawnSectorOrig;
            delete mapOverlay.mapHandler._twSpawnSectorOrig;
        } else if (mapOverlay.mapHandler._spawnSector) {
            TWMap.mapHandler.spawnSector = mapOverlay.mapHandler._spawnSector;
        }
        if (TWMap.map && TWMap.map._twHandleClickOrig) {
            TWMap.map._handleClick = TWMap.map._twHandleClickOrig;
            delete TWMap.map._twHandleClickOrig;
        } else if (mapOverlay.map && mapOverlay.map._DShandleClick) {
            TWMap.map._handleClick = mapOverlay.map._DShandleClick;
        }
        window.__twMapCoordPickerLoaded = false;
        try {
            TWMap.reload();
        } catch (e) {}
    }

    // spawnSector hook
    mapOverlay.mapHandler._twSpawnSectorOrig =
        mapOverlay.mapHandler._spawnSector || mapOverlay.mapHandler.spawnSector;
    mapOverlay.mapHandler._spawnSector = mapOverlay.mapHandler._twSpawnSectorOrig;
    TWMap.mapHandler.spawnSector = function (data, sector) {
        mapOverlay.mapHandler._twSpawnSectorOrig(data, sector);
        scanSectorVillages(data, sector);
        // Seçili köyleri yeniden vurgula
        var beginX = sector.x - data.x;
        var endX = beginX + mapOverlay.mapSubSectorSize;
        var beginY = sector.y - data.y;
        var endY = beginY + mapOverlay.mapSubSectorSize;
        var x, y, xCoord, yCoord, v, vCoords;
        for (x in data.tiles) {
            x = parseInt(x, 10);
            if (x < beginX || x >= endX) continue;
            for (y in data.tiles[x]) {
                y = parseInt(y, 10);
                if (y < beginY || y >= endY) continue;
                xCoord = data.x + x;
                yCoord = data.y + y;
                v = mapOverlay.villages[xCoord * 1000 + yCoord];
                if (!v || !selectedVillages.length) continue;
                vCoords = coordFromVillage(v, xCoord, yCoord);
                if (vCoords && selectedVillages.indexOf(vCoords) >= 0) {
                    highlightVillage(v, true);
                }
            }
        }
    };

    // Tıkla ekle/çıkar
    mapOverlay.map._DShandleClick = mapOverlay.map._handleClick;
    TWMap.map._twHandleClickOrig = mapOverlay.map._handleClick;
    TWMap.map._handleClick = function (e) {
        var pos = this.coordByEvent(e);
        var coord = pos.join('|');
        var village = TWMap.villages[pos[0] * 1000 + pos[1]];
        if (village && village.id) {
            if (selectedVillages.indexOf(coord) < 0) {
                selectedVillages.push(coord);
                highlightVillage(village, true);
            } else {
                selectedVillages = selectedVillages.filter(function (c) {
                    return c !== coord;
                });
                highlightVillage(village, false);
            }
            refreshList();
        }
        return false;
    };

    function ensureBridge(cb) {
        if (window.twMapCoordBridge && typeof window.twMapCoordBridge.setCoords === 'function') {
            cb();
            return;
        }
        if (!window.qt || !window.qt.webChannelTransport) {
            setTimeout(function () {
                ensureBridge(cb);
            }, 40);
            return;
        }
        if (window.__twMapCoordBridgeReady) {
            cb();
            return;
        }
        var s = document.createElement('script');
        s.src = 'qrc:///qtwebchannel/qwebchannel.js';
        s.onload = function () {
            new QWebChannel(qt.webChannelTransport, function (ch) {
                if (ch.objects.twMapCoordBridge) window.twMapCoordBridge = ch.objects.twMapCoordBridge;
                if (ch.objects.twPlannerBridge) window.twPlannerBridge = ch.objects.twPlannerBridge;
                window.__twMapCoordBridgeReady = 1;
                cb();
            });
        };
        (document.head || document.documentElement).appendChild(s);
    }

    buildFilterBar();
    buildFloatingUI();
    refreshList();
    ensureBridge(function () {});
})();
