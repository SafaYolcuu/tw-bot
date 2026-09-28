/**
 * Map Coord Picker — tw-bot
 * Filtreler: header flexible gap (köy / hammadde arası)
 * Sonuç: #map_config (sağ panel) + floating widget
 * Yalnızca o an görünen harita alanı (TWMap.map.pos + TWMap.size) taranır.
 * «Bot'a aktar» → twMapCoordBridge.setCoords
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
        jQuery(
            '#ra-map-coord-picker, #twMapCoordFilters, #twMapCoordResultPanel, #twMapCoordColorGroupPanel'
        ).remove();
        unwrapMapColorGroupLayout();
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

    /** Görünen harita dikdörtgeni (oyun koordinatı, inclusive). */
    function getVisibleBounds() {
        // Bazı sürümlerde hazır viewport
        if (TWMap.map && TWMap.map.viewport && TWMap.map.viewport.length >= 4) {
            var vp = TWMap.map.viewport;
            return {
                minX: Math.floor(Number(vp[0])),
                minY: Math.floor(Number(vp[1])),
                maxX: Math.floor(Number(vp[2])),
                maxY: Math.floor(Number(vp[3])),
            };
        }
        var map = TWMap.map;
        var size = TWMap.size;
        var tile = TWMap.tileSize || [53, 38];
        var minX, minY, w, h;
        if (map && map.pos && size && size[0] > 0 && size[1] > 0) {
            // pos = görünür alanın sol-üst köşesi (kaydırırken float olabilir)
            minX = Math.floor(Number(map.pos[0]));
            minY = Math.floor(Number(map.pos[1]));
            w = parseInt(size[0], 10) || 0;
            h = parseInt(size[1], 10) || 0;
        } else if (map && map.pos) {
            var el = document.getElementById('map');
            minX = Math.floor(Number(map.pos[0]));
            minY = Math.floor(Number(map.pos[1]));
            w = el ? Math.max(1, Math.ceil(el.clientWidth / (tile[0] || 53))) : 0;
            h = el ? Math.max(1, Math.ceil(el.clientHeight / (tile[1] || 38))) : 0;
        } else {
            return null;
        }
        if (!(w > 0 && h > 0) || isNaN(minX) || isNaN(minY)) return null;
        return {
            minX: minX,
            minY: minY,
            maxX: minX + w - 1,
            maxY: minY + h - 1,
        };
    }

    function isInVisibleBounds(x, y, bounds) {
        if (!bounds) return false;
        return (
            x >= bounds.minX &&
            x <= bounds.maxX &&
            y >= bounds.minY &&
            y <= bounds.maxY
        );
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
            jQuery('[id^=map_village_]').css({ filter: 'none' });
        }
        var bounds = getVisibleBounds();
        if (!bounds) {
            refreshList();
            return;
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
            if (!isInVisibleBounds(x, y, bounds)) continue;
            if (!villageMatchesFilters(v, fs)) continue;
            var coord = coordFromVillage(v, x, y);
            if (addCoord(coord)) highlightVillage(v, true);
        }
        refreshList();
    }

    function scanSectorVillages(data, sector) {
        var fs = filterState();
        if (!fs.any) return;
        var bounds = getVisibleBounds();
        if (!bounds) return;
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
                if (!isInVisibleBounds(xCoord, yCoord, bounds)) continue;
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
        lastViewportKey = '';
        selectedVillages = [];
        jQuery('[id^=map_village_]').css({ filter: 'none' });
        scanLoadedVillages({ clearFirst: true });
        try {
            TWMap.reload();
        } catch (e) {}
    }

    var lastViewportKey = '';
    var viewScanTimer = null;

    function viewportKey() {
        var map = TWMap.map;
        var size = TWMap.size || [0, 0];
        if (!map || !map.pos) return '';
        return (
            Math.floor(Number(map.pos[0])) +
            '|' +
            Math.floor(Number(map.pos[1])) +
            '|' +
            size[0] +
            'x' +
            size[1]
        );
    }

    /** Harita kayınca yalnızca yeni görünen eşleşmeleri ekle (mevcut liste silinmez). */
    function scheduleViewportRescan() {
        if (viewScanTimer) clearTimeout(viewScanTimer);
        viewScanTimer = setTimeout(function () {
            var fs = filterState();
            if (!fs.any) return;
            var key = viewportKey();
            if (!key || key === lastViewportKey) return;
            lastViewportKey = key;
            scanLoadedVillages({ clearFirst: false });
        }, 180);
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

    function uiError(msg) {
        if (typeof UI !== 'undefined' && UI.ErrorMessage) UI.ErrorMessage(msg, 4000);
        else window.alert(msg);
    }

    function uiOk(msg) {
        if (typeof UI !== 'undefined' && UI.SuccessMessage) UI.SuccessMessage(msg, 3500);
    }

    function showVillageColors() {
        var $vc = jQuery('#village_colors');
        if ($vc.length && !$vc.is(':visible')) $vc.show();
    }

    function randomBrightHex() {
        var r = 80 + Math.floor(Math.random() * 175);
        var g = 80 + Math.floor(Math.random() * 175);
        var b = 80 + Math.floor(Math.random() * 175);
        function h(n) {
            var s = n.toString(16);
            return s.length === 1 ? '0' + s : s;
        }
        return '#' + h(r) + h(g) + h(b);
    }

    function hexToRgb(hex) {
        var m = String(hex || '')
            .replace('#', '')
            .match(/^([0-9a-fA-F]{6})$/);
        if (!m) return { r: 254, g: 0, b: 0 };
        var n = m[1];
        return {
            r: parseInt(n.slice(0, 2), 16),
            g: parseInt(n.slice(2, 4), 16),
            b: parseInt(n.slice(4, 6), 16),
        };
    }

    function existingOtherGroupIds() {
        var ids = {};
        jQuery('#for_color_groups .colorgroup-other-entry').each(function () {
            var id = jQuery(this).attr('data-id');
            if (id != null && id !== '') ids[String(id)] = true;
        });
        return ids;
    }

    function refreshGroupSelect(preferId) {
        var $sel = jQuery('#twMapCoordGroupSelect');
        if (!$sel.length) return;
        var prev = preferId != null ? String(preferId) : $sel.val();
        $sel.empty();
        $sel.append(jQuery('<option></option>').attr('value', '__new__').text('Yeni grup…'));
        jQuery('#for_color_groups .colorgroup-other-entry').each(function () {
            var $row = jQuery(this);
            var id = String($row.attr('data-id') || '');
            if (!id) return;
            var name = jQuery('#groupname_' + id).text().trim() || ('Grup ' + id);
            $sel.append(jQuery('<option></option>').attr('value', id).text(name));
        });
        if (prev && $sel.find('option[value="' + prev + '"]').length) {
            $sel.val(prev);
        } else if ($sel.find('option').length > 1) {
            $sel.prop('selectedIndex', 1);
        } else {
            $sel.val('__new__');
        }
        toggleNewGroupFields();
    }

    function toggleNewGroupFields() {
        var isNew = jQuery('#twMapCoordGroupSelect').val() === '__new__';
        jQuery('#twMapCoordNewGroupFields').toggle(!!isNew);
        if (isNew) {
            var $name = jQuery('#twMapCoordNewGroupName');
            if ($name.length && !jQuery.trim($name.val())) {
                var d = new Date();
                var pad = function (n) {
                    return n < 10 ? '0' + n : String(n);
                };
                $name.val(
                    'Seçim ' +
                        pad(d.getHours()) +
                        ':' +
                        pad(d.getMinutes())
                );
            }
            if (!jQuery('#twMapCoordNewGroupColor').val()) {
                jQuery('#twMapCoordNewGroupColor').val(randomBrightHex());
            }
        }
    }

    function bindOtherEntryRow($row) {
        if (!$row || !$row.length || typeof ColorGroups === 'undefined') return;
        var id = $row.attr('data-id');
        if (id == null) return;
        id = parseInt(id, 10);
        $row
            .find('.colorgroup-other-activate')
            .off('change.twmcp')
            .on('change.twmcp', function () {
                if (jQuery(this).is(':checked')) ColorGroups.Other.activateGroup(id);
                else ColorGroups.Other.deactivateGroup(id);
            });
        $row
            .find('.colorgroup-other-delete')
            .off('click.twmcp')
            .on('click.twmcp', function (e) {
                e.preventDefault();
                UI.addConfirmBox(
                    typeof _ === 'function'
                        ? _('1d7a209e5461c92be4a38e7cfef02380')
                        : 'Bu grubu silmek istiyor musun?',
                    function () {
                        ColorGroups.Other.deleteGroup(id);
                    }
                );
            });
        $row
            .find('.color_picker_launcher')
            .off('click.twmcp')
            .on('click.twmcp', function (e) {
                e.preventDefault();
                ColorGroups.color_picker.openPopup(jQuery(this), ColorGroups.TYPE_OTHER);
            });
    }

    function paintAddedVillages(resp) {
        if (!resp || !resp.villages || !resp.villages.length) return;
        resp.villages.forEach(function (item) {
            if (typeof MapHighlighter === 'undefined') return;
            MapHighlighter.alterVillage(item.village_id, item.color);
            if (typeof TWMap.villageKey[item.village_id] !== 'undefined') {
                MapHighlighter.colorVillage(TWMap.villages[TWMap.villageKey[item.village_id]]);
            }
            TWMap.minimap_cache_stamp++;
        });
        try {
            TWMap.minimap.reload(true);
        } catch (e1) {}
        try {
            TWMap.map.reload(true);
        } catch (e2) {}
    }

    function addVillagesToGroup(groupId, coordsNl, done) {
        TribalWars.post(
            'map',
            { ajaxaction: 'colorgroup_add_multiple_villages' },
            { group_id: groupId, coordinates: coordsNl },
            function (resp) {
                paintAddedVillages(resp);
                if (typeof done === 'function') done(resp);
            }
        );
    }

    function createForGroup(name, done) {
        var $form = jQuery('#new_group form');
        if (!$form.length) {
            uiError('Yeni grup formu bulunamadı (Harita vurguları).');
            return;
        }
        var before = existingOtherGroupIds();
        var url = $form.attr('action');
        var data = $form.serializeArray();
        var hasName = false;
        var i;
        for (i = 0; i < data.length; i++) {
            if (data[i].name === 'new_group_name') {
                data[i].value = name;
                hasName = true;
            }
        }
        if (!hasName) data.push({ name: 'new_group_name', value: name });
        data.push({ name: 'for_new_group', value: 'Oluştur' });

        jQuery.ajax({
            url: url,
            type: 'POST',
            data: jQuery.param(data),
            dataType: 'html',
            success: function (html) {
                var $entries = jQuery();
                try {
                    var doc = new DOMParser().parseFromString(html, 'text/html');
                    $entries = jQuery(doc).find('#for_color_groups .colorgroup-other-entry');
                } catch (parseErr) {
                    var $parsed = jQuery('<div>').append(jQuery.parseHTML(html));
                    $entries = $parsed.find('#for_color_groups .colorgroup-other-entry');
                }
                var $newRow = null;
                $entries.each(function () {
                    var id = String(jQuery(this).attr('data-id') || '');
                    if (id && !before[id]) {
                        $newRow = jQuery(this);
                        return false;
                    }
                });
                if (!$newRow || !$newRow.length) {
                    $newRow = $entries.last();
                    if ($newRow.length && before[String($newRow.attr('data-id'))]) {
                        $newRow = null;
                    }
                }
                if (!$newRow || !$newRow.length) {
                    uiError('Yeni grup oluşturuldu ama satır okunamadı. Sayfayı yenileyip tekrar dene.');
                    return;
                }
                var $clone = jQuery($newRow.prop('outerHTML'));
                jQuery('#for_color_groups').append($clone);
                bindOtherEntryRow($clone);
                var groupId = String($clone.attr('data-id'));
                if (typeof done === 'function') done(groupId, $clone);
            },
            error: function () {
                uiError('Grup oluşturma isteği başarısız.');
            },
        });
    }

    function pushSelectionToColorGroup() {
        if (typeof ColorGroups === 'undefined' || !ColorGroups.Other) {
            uiError('Renk grupları (ColorGroups) yok — PA / Harita vurguları gerekli.');
            return;
        }
        if (!selectedVillages.length) {
            uiError('Seçili köy yok.');
            return;
        }
        showVillageColors();

        var sel = jQuery('#twMapCoordGroupSelect').val();
        var coordsNl = selectedVillages.join('\n');
        var count = selectedVillages.length;

        function afterAdd(groupId, createdNew, rgb) {
            function finish(resp) {
                var ok = !resp || resp.status !== false;
                if (ok) {
                    uiOk(
                        count +
                            ' köy gruba eklendi' +
                            (createdNew ? ' (yeni grup)' : '') +
                            '.'
                    );
                } else if (resp && resp.message) {
                    uiError(resp.message);
                } else {
                    uiError('Köyler eklenemedi.');
                }
                refreshGroupSelect(groupId);
            }

            function doAdd() {
                addVillagesToGroup(groupId, coordsNl, finish);
            }

            if (createdNew && rgb) {
                TribalWars.post(
                    'map',
                    { ajaxaction: 'colorgroup_change_color' },
                    { group_id: groupId, r: rgb.r, g: rgb.g, b: rgb.b },
                    function (e) {
                        try {
                            MapLegend.updateHighlight(
                                MapLegend.CATEGORY_OTHER,
                                groupId,
                                e.group_name,
                                { r: rgb.r, g: rgb.g, b: rgb.b }
                            );
                        } catch (err1) {}
                        try {
                            ColorGroups.Other.handleBigChange(e);
                        } catch (err2) {}
                        var $row = jQuery('.colorgroup-other-entry[data-id="' + groupId + '"]');
                        $row
                            .find('.marker')
                            .css(
                                'background-color',
                                'rgb(' + rgb.r + ',' + rgb.g + ',' + rgb.b + ')'
                            );
                        $row
                            .find('.color_picker_launcher')
                            .data('r', rgb.r)
                            .data('g', rgb.g)
                            .data('b', rgb.b);
                        doAdd();
                    }
                );
            } else {
                doAdd();
            }
        }

        if (sel === '__new__') {
            var name = jQuery.trim(jQuery('#twMapCoordNewGroupName').val() || '');
            if (!name) {
                uiError('Yeni grup için bir ad yaz.');
                return;
            }
            var rgb = hexToRgb(jQuery('#twMapCoordNewGroupColor').val());
            createForGroup(name, function (groupId) {
                refreshGroupSelect(groupId);
                afterAdd(groupId, true, rgb);
            });
            return;
        }

        if (!sel) {
            uiError('Bir grup seç veya Yeni grup… seç.');
            return;
        }
        afterAdd(sel, false, null);
    }

    function unwrapMapColorGroupLayout() {
        var $wrap = jQuery('#twMapCoordMapRow');
        if (!$wrap.length) return;
        var $table = $wrap.children('table.map_container').first();
        if ($table.length) $table.insertBefore($wrap);
        $wrap.remove();
    }

    function buildColorGroupUI() {
        jQuery('#twMapCoordColorGroupPanel').remove();
        unwrapMapColorGroupLayout();
        if (!jQuery('#for_groups').length && typeof ColorGroups === 'undefined') return;

        var html =
            '<div id="twMapCoordColorGroupPanel" style="' +
            'flex:0 0 168px;width:168px;max-width:168px;box-sizing:border-box;' +
            'padding:8px 6px;margin:0;font-size:11px;line-height:1.3;' +
            'background:#f4e4bc;border:1px solid #8c5f0d;align-self:flex-start;' +
            '">' +
            '<div style="font-weight:600;margin-bottom:6px;">Seçili köyleri gruba ekle</div>' +
            '<label style="display:block;margin-bottom:6px;">Grup<br/>' +
            '<select id="twMapCoordGroupSelect" style="width:100%;margin-top:2px;box-sizing:border-box;"></select></label>' +
            '<div id="twMapCoordNewGroupFields" style="display:none;margin:4px 0 6px;">' +
            '<label style="display:block;margin-bottom:4px;">Yeni grup adı<br/>' +
            '<input type="text" id="twMapCoordNewGroupName" placeholder="Grup adı" ' +
            'style="width:100%;margin-top:2px;box-sizing:border-box;" /></label>' +
            '<label style="display:inline-flex;align-items:center;gap:6px;">Renk ' +
            '<input type="color" id="twMapCoordNewGroupColor" value="' +
            randomBrightHex() +
            '" /></label>' +
            '</div>' +
            '<a href="#" class="btn btn-confirm-yes" id="twMapCoordToColorGroup" style="display:inline-block;margin-top:2px;">Seçimi ekle</a>' +
            '</div>';

        var $mapTable = jQuery('#map').closest('table.map_container');
        if ($mapTable.length) {
            var $wrap = jQuery(
                '<div id="twMapCoordMapRow" style="display:flex;align-items:flex-start;gap:8px;clear:both;"></div>'
            );
            $mapTable.before($wrap);
            $wrap.append(html);
            $wrap.append($mapTable);
        } else if (jQuery('#map_wrap').length) {
            jQuery('#map_wrap').before(html);
        } else {
            var $after = jQuery('#for_groups').nextAll('a').filter(function () {
                return /Yeni grup/i.test(jQuery(this).text());
            }).first();
            if ($after.length) $after.after(html);
            else if (jQuery('#for_groups').length) jQuery('#for_groups').after(html);
            else return;
        }

        refreshGroupSelect();
        jQuery('#twMapCoordGroupSelect').on('change', toggleNewGroupFields);
        jQuery('#twMapCoordToColorGroup').on('click', function (e) {
            e.preventDefault();
            pushSelectionToColorGroup();
        });
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
            '<a href="#" class="btn" id="twMapCoordGroupBtn">Gruba ekle</a>' +
            '</div>' +
            '<p style="margin:8px 0 0;font-size:10px;color:#444;">Yalnızca ekranda görünen alan taranır; gezinince yeni eşleşmeler listeye eklenir (eski silinmez). Sıfırla ile temizle. Köye tık = elle ekle/çıkar. Gruba ekle = Harita vurguları (Yabancı köyler).</p>' +
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
        jQuery('#twMapCoordGroupBtn').on('click', function (e) {
            e.preventDefault();
            showVillageColors();
            refreshGroupSelect();
            pushSelectionToColorGroup();
        });

        try {
            jQuery('#' + id).draggable({ cancel: 'textarea, input, select, .btn' });
        } catch (e2) {}
    }

    function teardown() {
        jQuery(
            '#ra-map-coord-picker, #twMapCoordFilters, #twMapCoordResultPanel, #twMapCoordColorGroupPanel'
        ).remove();
        unwrapMapColorGroupLayout();
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
        scheduleViewportRescan();
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
    buildColorGroupUI();
    refreshList();
    ensureBridge(function () {});
})();
