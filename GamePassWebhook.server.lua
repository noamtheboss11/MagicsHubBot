local HttpService = game:GetService("HttpService")
local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local CONFIG = {
    bootstrapUrl = "https://magicshubbot.onrender.com/api/roblox/game/bootstrap",
    webhookUrl = "https://magicshubbot.onrender.com/webhooks/roblox/gamepass",
    webhookToken = "replace-with-your-roblox-gamepass-webhook-token",
    checkIntervalSeconds = 5,
    catalogRefreshIntervalSeconds = 60,
    unlinkedKickMessage =
    "אין לך חשבון דיסקורד מחובר. אנא לך לשרת שלנו ותחבר אותו בעזרת המדריך בחדר \"חיבור משתמש\" וחזור לכאן לאחר מכן.",
    verificationFailedKickMessage = "לא היה ניתן לאמת את חיבור החשבון כרגע. נסה שוב בעוד כמה רגעים.",
}

local REMOTE_FOLDER_NAME = "MagicStudioShop"
local BOOTSTRAP_REMOTE_NAME = "Bootstrap"
local STATE_UPDATED_EVENT_NAME = "StateUpdated"

local function ensureChildOfType(parent, name, className)
    local instance = parent:FindFirstChild(name)
    if instance ~= nil and not instance:IsA(className) then
        instance:Destroy()
        instance = nil
    end

    if instance == nil then
        instance = Instance.new(className)
        instance.Name = name
        instance.Parent = parent
    end

    return instance
end

local remoteFolder = ReplicatedStorage:FindFirstChild(REMOTE_FOLDER_NAME)
if remoteFolder == nil then
    remoteFolder = Instance.new("Folder")
    remoteFolder.Name = REMOTE_FOLDER_NAME
    remoteFolder.Parent = ReplicatedStorage
end

local bootstrapRemote = ensureChildOfType(remoteFolder, BOOTSTRAP_REMOTE_NAME, "RemoteFunction")
local stateUpdatedEvent = ensureChildOfType(remoteFolder, STATE_UPDATED_EVENT_NAME, "RemoteEvent")

local synchronizedProducts = {}
local productsByGamePassId = {}
local playerSessions = {}

local function getPlayerSession(player)
    local session = playerSessions[player.UserId]
    if session == nil then
        session = {
            rewardsGranted = {},
            webhooksSent = {},
            connectedAccount = "",
            discordUserId = nil,
            lastBootstrapAt = 0,
        }
        playerSessions[player.UserId] = session
    end
    return session
end

local function cloneProducts(products)
    local clonedProducts = {}
    for index, product in ipairs(products) do
        clonedProducts[index] = {
            Title = product.Title,
            Description = product.Description,
            GamePassID = product.GamePassID,
            Price = product.Price,
            Thumbnail = product.Thumbnail,
            SystemId = product.SystemId,
            SystemName = product.SystemName,
            IsForSale = product.IsForSale,
            PurchaseUrl = product.PurchaseUrl,
        }
    end
    return clonedProducts
end

local function buildClientPayload(player)
    local session = getPlayerSession(player)
    return {
        Products = cloneProducts(synchronizedProducts),
        ConnectedAccount = session.connectedAccount or "",
    }
end

local function broadcastState()
    for _, player in ipairs(Players:GetPlayers()) do
        if player.Parent == Players then
            stateUpdatedEvent:FireClient(player, buildClientPayload(player))
        end
    end
end

local function applyCatalog(catalog)
    local nextProducts = {}
    local nextProductsByGamePassId = {}

    if type(catalog) ~= "table" then
        synchronizedProducts = nextProducts
        productsByGamePassId = nextProductsByGamePassId
        return
    end

    for _, entry in ipairs(catalog) do
        local gamePassId = tonumber(entry.gamepass_id or entry.GamePassID)
        if gamePassId ~= nil then
            local title = tostring(entry.title or entry.Title or entry.system_name or "Unnamed Product")
            local product = {
                Title = title,
                Description = tostring(entry.description or entry.Description or ""),
                GamePassID = gamePassId,
                Price = tonumber(entry.price or entry.price_in_robux or entry.Price),
                Thumbnail = tostring(entry.thumbnail or entry.Thumbnail or ""),
                SystemId = tonumber(entry.system_id or entry.SystemId),
                SystemName = tostring(entry.system_name or entry.SystemName or title),
                IsForSale = entry.is_for_sale ~= false,
                PurchaseUrl = tostring(entry.purchase_url or entry.PurchaseUrl or ""),
            }
            table.insert(nextProducts, product)
            nextProductsByGamePassId[gamePassId] = product
        end
    end

    table.sort(nextProducts, function(left, right)
        return string.lower(left.Title) < string.lower(right.Title)
    end)

    synchronizedProducts = nextProducts
    productsByGamePassId = nextProductsByGamePassId
end

local function postJson(url, payload)
    local requestBody = HttpService:JSONEncode(payload)

    local success, response = pcall(function()
        return HttpService:RequestAsync({
            Url = url,
            Method = "POST",
            Headers = {
                ["Content-Type"] = "application/json",
                ["X-Roblox-Webhook-Token"] = CONFIG.webhookToken,
            },
            Body = requestBody,
        })
    end)

    if not success then
        return nil, tostring(response)
    end

    if not response.Success then
        return nil, string.format("%s %s", tostring(response.StatusCode), tostring(response.Body))
    end

    local decodedSuccess, decodedBody = pcall(function()
        if response.Body == nil or response.Body == "" then
            return {}
        end
        return HttpService:JSONDecode(response.Body)
    end)

    if not decodedSuccess then
        return nil, tostring(decodedBody)
    end

    return decodedBody, nil
end

local function fetchBootstrap(player)
    return postJson(CONFIG.bootstrapUrl, {
        roblox_user_id = player.UserId,
    })
end

local function syncPlayerSession(player)
    local data, err = fetchBootstrap(player)
    if data == nil then
        warn(string.format("[GamePassWebhook] Bootstrap sync failed for %s (%d): %s", player.Name, player.UserId,
            tostring(err)))
        player:Kick(CONFIG.verificationFailedKickMessage)
        return false
    end

    applyCatalog(data.catalog)

    local playerData = data.player
    if type(playerData) ~= "table" or playerData.is_linked ~= true then
        player:Kick(CONFIG.unlinkedKickMessage)
        return false
    end

    local session = getPlayerSession(player)
    session.connectedAccount = tostring(playerData.connected_account or "")
    session.discordUserId = tonumber(playerData.discord_user_id)
    session.lastBootstrapAt = os.time()

    broadcastState()
    print(string.format(
        "[GamePassWebhook] Synced %d products for %s. Connected Discord account: %s",
        #synchronizedProducts,
        player.Name,
        session.connectedAccount ~= "" and session.connectedAccount or "unknown"
    ))
    return true
end

local function postWebhook(player, gamePassId)
    local responseBody, err = postJson(CONFIG.webhookUrl, {
        roblox_user_id = player.UserId,
        gamepass_id = gamePassId,
    })

    if responseBody == nil then
        warn(string.format(
            "[GamePassWebhook] Request failed for user %d and game pass %d: %s",
            player.UserId,
            gamePassId,
            tostring(err)
        ))
        return false
    end

    print(string.format("[GamePassWebhook] Webhook accepted for %s and game pass %d", player.Name, gamePassId))
    return true
end

local function grantGamePassReward(player, product)
    print(string.format(
        "[GamePassWebhook] Purchase detected for %s. Discord delivery is handled automatically for game pass %d.",
        player.Name,
        tonumber(product.GamePassID) or 0
    ))
end

local function handleOwnedGamePass(player, product, source)
    local gamePassId = tonumber(product.GamePassID)
    if gamePassId == nil then
        return
    end

    local session = getPlayerSession(player)

    if not session.rewardsGranted[gamePassId] then
        local rewardSuccess, rewardError = pcall(function()
            grantGamePassReward(player, product)
        end)

        if rewardSuccess then
            session.rewardsGranted[gamePassId] = true
            print(string.format(
                "[GamePassWebhook] Reward logic completed for %s, game pass %d via %s",
                player.Name,
                gamePassId,
                source
            ))
        else
            warn(string.format(
                "[GamePassWebhook] Reward logic failed for %s and game pass %d via %s: %s",
                player.Name,
                gamePassId,
                source,
                tostring(rewardError)
            ))
        end
    end

    if not session.webhooksSent[gamePassId] then
        local webhookSuccess = postWebhook(player, gamePassId)
        if webhookSuccess then
            session.webhooksSent[gamePassId] = true
        end
    end
end

local function checkOwnedGamePass(player, product)
    local success, result = pcall(function()
        return MarketplaceService:UserOwnsGamePassAsync(player.UserId, product.GamePassID)
    end)

    if not success then
        warn(string.format(
            "[GamePassWebhook] Ownership check failed for user %d and game pass %d: %s",
            player.UserId,
            product.GamePassID,
            tostring(result)
        ))
        return
    end

    if result then
        handleOwnedGamePass(player, product, "ownership-check")
    end
end

local function checkAllWatchedGamePasses(player)
    for _, product in ipairs(synchronizedProducts) do
        checkOwnedGamePass(player, product)
    end
end

local function monitorPlayer(player)
    task.spawn(function()
        local synced = syncPlayerSession(player)
        if not synced or player.Parent ~= Players then
            playerSessions[player.UserId] = nil
            return
        end

        checkAllWatchedGamePasses(player)

        while player.Parent == Players do
            task.wait(CONFIG.checkIntervalSeconds)

            local session = getPlayerSession(player)
            if os.time() - session.lastBootstrapAt >= CONFIG.catalogRefreshIntervalSeconds then
                local refreshed = syncPlayerSession(player)
                if not refreshed or player.Parent ~= Players then
                    break
                end
            end

            checkAllWatchedGamePasses(player)
        end

        playerSessions[player.UserId] = nil
    end)
end

bootstrapRemote.OnServerInvoke = function(player)
    local session = playerSessions[player.UserId]
    if session == nil or session.lastBootstrapAt == 0 then
        local synced = syncPlayerSession(player)
        if not synced then
            return {
                Products = {},
                ConnectedAccount = "",
            }
        end
    end

    return buildClientPayload(player)
end

MarketplaceService.PromptGamePassPurchaseFinished:Connect(function(player, gamePassId, wasPurchased)
    if not wasPurchased then
        return
    end

    local product = productsByGamePassId[gamePassId]
    if product == nil then
        return
    end

    handleOwnedGamePass(player, product, "prompt-finished")
end)

Players.PlayerAdded:Connect(monitorPlayer)
Players.PlayerRemoving:Connect(function(player)
    playerSessions[player.UserId] = nil
end)

for _, player in ipairs(Players:GetPlayers()) do
    monitorPlayer(player)
end

print("GamePass Webhook System initialized successfully!")
