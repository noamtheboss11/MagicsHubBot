local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local MarketplaceService = game:GetService("MarketplaceService")
local UserInputService = game:GetService("UserInputService")
local StarterGui = game:GetService("StarterGui")

local Player = Players.LocalPlayer
local PlayerGui = Player:WaitForChild("PlayerGui")

local Template = ReplicatedStorage:WaitForChild("Template")
local ShopFolder = ReplicatedStorage:WaitForChild("MagicStudioShop")
local BootstrapRemote = ShopFolder:WaitForChild("Bootstrap")
local StateUpdatedEvent = ShopFolder:WaitForChild("StateUpdated")

local MainUI = PlayerGui:WaitForChild("MainUI")
local MainHubFrame = MainUI:WaitForChild("MainHubFrame")
local ProductsScrolling = MainHubFrame:WaitForChild("ProductsScrolling")
local ConnectedAccount = MainHubFrame:WaitForChild("ConnectedAccount")

local success, err = pcall(function()
    StarterGui:SetCore("ResetButtonCallback", false)
end)
if success then
    print("Reset button disabled")
else
    warn("Could not disable reset button:", err)
end

local function setupGridLayout()
    local existingLayout = ProductsScrolling:FindFirstChildOfClass("UIGridLayout")
    if existingLayout then
        existingLayout:Destroy()
    end

    local isMobile = UserInputService.TouchEnabled
    local gridLayout
    if isMobile then
        gridLayout = ReplicatedStorage:FindFirstChild("[Mobile]UIGridLayout")
        print("Mobile platform detected - using mobile grid layout")
    else
        gridLayout = ReplicatedStorage:FindFirstChild("[PC]UIGridLayout")
        print("PC platform detected - using PC grid layout")
    end

    if gridLayout then
        gridLayout:Clone().Parent = ProductsScrolling
    else
        warn("Could not find appropriate UIGridLayout")
    end
end

local function clearProductCards()
    for _, child in ipairs(ProductsScrolling:GetChildren()) do
        if child:IsA("GuiObject") then
            child:Destroy()
        end
    end
end

local function renderProducts(products)
    clearProductCards()
    setupGridLayout()

    if type(products) ~= "table" then
        return
    end

    for _, product in ipairs(products) do
        local productCard = Template:Clone()
        local gamePassID = tonumber(product.GamePassID)
        productCard.Name = tostring(gamePassID or product.Title or "Product")

        local titleLabel = productCard:FindFirstChild("Title")
        local thumbnailLabel = productCard:FindFirstChild("Thumbnail")
        local purchaseButton = productCard:FindFirstChild("Purchase")

        if titleLabel then
            local priceLabel = product.Price and string.format("%d R$", product.Price) or "Price unavailable"
            titleLabel.Text = string.format("%s\n%s", tostring(product.Title or "Unnamed Product"), priceLabel)
        end

        if thumbnailLabel and type(product.Thumbnail) == "string" and product.Thumbnail ~= "" then
            thumbnailLabel.Image = product.Thumbnail
        end

        if purchaseButton then
            local canPurchase = gamePassID ~= nil and product.IsForSale ~= false
            purchaseButton.Active = canPurchase
            purchaseButton.AutoButtonColor = canPurchase
            if purchaseButton:IsA("TextButton") then
                purchaseButton.Text = canPurchase and "Buy" or "Off Sale"
            end

            purchaseButton.Activated:Connect(function()
                if not canPurchase or gamePassID == nil then
                    return
                end

                print("Prompting gamepass purchase for:", tostring(product.Title), "with ID:", gamePassID)
                MarketplaceService:PromptGamePassPurchase(Player, gamePassID)
            end)
        end

        productCard.Parent = ProductsScrolling
    end
end

local function renderState(state)
    if type(state) ~= "table" then
        return
    end

    local connectedAccountText = tostring(state.ConnectedAccount or "")
    if connectedAccountText ~= "" then
        ConnectedAccount.Text = "Connected Discord: " .. connectedAccountText
    else
        ConnectedAccount.Text = "Connected Discord: not available"
    end

    renderProducts(state.Products)
end

local initialStateSuccess, initialState = pcall(function()
    return BootstrapRemote:InvokeServer()
end)
if initialStateSuccess then
    renderState(initialState)
else
    warn("Could not bootstrap Roblox shop state:", initialState)
end

StateUpdatedEvent.OnClientEvent:Connect(renderState)

print("GamePass Client System initialized successfully!")
