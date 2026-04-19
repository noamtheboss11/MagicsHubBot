local HttpService = game:GetService("HttpService")
local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local ProductsModule = require(ReplicatedStorage:WaitForChild("ProductsModule"))

local CONFIG = {
    webhookUrl = "https://your-render-domain.onrender.com/webhooks/roblox/gamepass",
    webhookToken = "replace-with-your-roblox-gamepass-webhook-token",
    checkIntervalSeconds = 60,
}

local productsByGamePassId = {}
for _, product in ipairs(ProductsModule.GetAllProducts()) do
    local gamePassId = tonumber(product.GamePassID)
    if gamePassId ~= nil then
        productsByGamePassId[gamePassId] = product
    end
end

local sessionOwnershipState = {}

local function getPlayerState(player)
    local state = sessionOwnershipState[player.UserId]
    if state == nil then
        state = {
            rewardsGranted = {},
            webhooksSent = {},
        }
        sessionOwnershipState[player.UserId] = state
    end
    return state
end

local function postWebhook(player, gamePassId)
    local payload = HttpService:JSONEncode({
        roblox_user_id = player.UserId,
        gamepass_id = gamePassId,
    })

    local success, response = pcall(function()
        return HttpService:RequestAsync({
            Url = CONFIG.webhookUrl,
            Method = "POST",
            Headers = {
                ["Content-Type"] = "application/json",
                ["X-Roblox-Webhook-Token"] = CONFIG.webhookToken,
            },
            Body = payload,
        })
    end)

    if not success then
        warn(string.format("[GamePassWebhook] Request failed for user %d and game pass %d: %s", player.UserId, gamePassId,
            tostring(response)))
        return false
    end

    if not response.Success then
        warn(string.format(
            "[GamePassWebhook] Webhook rejected for user %d and game pass %d: %s %s",
            player.UserId,
            gamePassId,
            tostring(response.StatusCode),
            tostring(response.Body)
        ))
        return false
    end

    print(string.format("[GamePassWebhook] Webhook accepted for %s and game pass %d", player.Name, gamePassId))
    return true
end

local function grantGamePassReward(player, product)
    print("Processing gamepass purchase for player:", player.Name)
    print("Gamepass:", product.Title)
    print("GamePass ID:", product.GamePassID)

    -- Add your gamepass reward logic here.
    -- Example: give the player an item, unlock a feature, or save ownership data.
end

local function handleOwnedGamePass(player, product, source)
    local gamePassId = tonumber(product.GamePassID)
    if gamePassId == nil then
        return
    end

    local state = getPlayerState(player)

    if not state.rewardsGranted[gamePassId] then
        local rewardSuccess, rewardError = pcall(function()
            grantGamePassReward(player, product)
        end)

        if rewardSuccess then
            state.rewardsGranted[gamePassId] = true
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

    if not state.webhooksSent[gamePassId] then
        local webhookSuccess = postWebhook(player, gamePassId)
        if webhookSuccess then
            state.webhooksSent[gamePassId] = true
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
    for _, product in pairs(productsByGamePassId) do
        checkOwnedGamePass(player, product)
    end
end

local function monitorPlayer(player)
    task.spawn(function()
        checkAllWatchedGamePasses(player)

        while player.Parent == Players do
            task.wait(CONFIG.checkIntervalSeconds)
            checkAllWatchedGamePasses(player)
        end

        sessionOwnershipState[player.UserId] = nil
    end)
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

for _, player in ipairs(Players:GetPlayers()) do
    monitorPlayer(player)
end

print(string.format("GamePass Webhook System initialized successfully with %d tracked game passes!",
    #ProductsModule.GetAllProducts()))
