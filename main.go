package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const SettingsFile = "setting.json"

type EmergentConfig struct {
	APIURL string `json:"api_url"`
	AppURL string `json:"app_url"`
}

type ToolUseConfig struct {
	BaseURL string `json:"base_url"`
	APIKey  string `json:"api_key"`
	Model   string `json:"model"`
}

type RegistrationConfig struct {
	EmailAPIURL    string `json:"email_api_url"`
	EmailAPIKey    string `json:"email_api_key"`
	SupabaseAPIKey string `json:"supabase_api_key"`
	BaseAuthURL    string `json:"base_auth_url"`
	MinAccounts    int    `json:"min_accounts"`
	MaxAccounts    int    `json:"max_accounts"`
}

type Settings struct {
	ListenAddr          string             `json:"listen_addr"`
	AccountsFile        string             `json:"accounts_file"`
	PollIntervalSeconds int                `json:"poll_interval_seconds"`
	MaxPollAttempts     int                `json:"max_poll_attempts"`
	Emergent            EmergentConfig     `json:"emergent"`
	ToolUse             ToolUseConfig      `json:"tool_use"`
	Registration        RegistrationConfig `json:"registration"`
}

func loadSettings() Settings {
	defaults := Settings{
		ListenAddr:          ":8001",
		AccountsFile:        "accounts.json",
		PollIntervalSeconds: 2,
		MaxPollAttempts:     60,
		Emergent: EmergentConfig{
			APIURL: "https://api.emergent.sh",
			AppURL: "https://app.emergent.sh",
		},
		ToolUse: ToolUseConfig{
			BaseURL: "",
			APIKey:  "",
			Model:   "gpt-4o",
		},
		Registration: RegistrationConfig{
			EmailAPIURL:    "",
			EmailAPIKey:    "",
			SupabaseAPIKey: "",
			BaseAuthURL:    "https://auth.emergent.sh",
			MinAccounts:    3,
			MaxAccounts:    3,
		},
	}
	data, err := os.ReadFile(SettingsFile)
	if err != nil {
		log.Printf("[settings] %s not found, using defaults", SettingsFile)
		return defaults
	}
	if err := json.Unmarshal(data, &defaults); err != nil {
		log.Printf("[settings] parse error: %v, using defaults", err)
	}
	return defaults
}

var cfg Settings

type Account struct {
	JWT           string  `json:"jwt"`
	Email         string  `json:"email"`
	CreatedAt     float64 `json:"created_at"`
	TotalRequests int64   `json:"total_requests"`
	IsActive      bool    `json:"is_active"`
}

type AccountPool struct {
	mu       sync.RWMutex
	accounts []*Account
	current  uint64
}

func NewAccountPool(file string) *AccountPool {
	p := &AccountPool{}
	data, err := os.ReadFile(file)
	if err != nil {
		log.Printf("[pool] no accounts file: %v", err)
		return p
	}
	var accounts []Account
	if err := json.Unmarshal(data, &accounts); err != nil {
		log.Printf("[pool] parse error: %v", err)
		return p
	}
	for i := range accounts {
		if accounts[i].IsActive {
			p.accounts = append(p.accounts, &accounts[i])
		}
	}
	log.Printf("[pool] loaded %d active accounts", len(p.accounts))
	return p
}

func (p *AccountPool) Get() *Account {
	p.mu.RLock()
	defer p.mu.RUnlock()
	if len(p.accounts) == 0 {
		return nil
	}
	idx := atomic.AddUint64(&p.current, 1)
	acc := p.accounts[int(idx)%len(p.accounts)]
	atomic.AddInt64(&acc.TotalRequests, 1)
	return acc
}

func (p *AccountPool) Deactivate(jwt string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, acc := range p.accounts {
		if acc.JWT == jwt {
			acc.IsActive = false
			log.Printf("[pool] deactivated %s", acc.Email)
		}
	}
	var active []*Account
	for _, a := range p.accounts {
		if a.IsActive {
			active = append(active, a)
		}
	}
	p.accounts = active
	p.saveToFile()
}

func (p *AccountPool) saveToFile() {
	data, err := os.ReadFile(cfg.AccountsFile)
	if err != nil {
		log.Printf("[pool] saveToFile read error: %v", err)
		return
	}
	var all []Account
	if err := json.Unmarshal(data, &all); err != nil {
		log.Printf("[pool] saveToFile parse error: %v", err)
		return
	}
	active := make(map[string]bool)
	for _, a := range p.accounts {
		active[a.JWT] = true
	}
	for i := range all {
		all[i].IsActive = active[all[i].JWT]
	}
	out, err := json.MarshalIndent(all, "", "  ")
	if err != nil {
		log.Printf("[pool] saveToFile marshal error: %v", err)
		return
	}
	if err := os.WriteFile(cfg.AccountsFile, out, 0644); err != nil {
		log.Printf("[pool] saveToFile write error: %v", err)
		return
	}
	log.Printf("[pool] saved %d accounts to %s", len(all), cfg.AccountsFile)
}

func (p *AccountPool) ActiveCount() int {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return len(p.accounts)
}

func (p *AccountPool) AddAccount(acc *Account) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.accounts = append(p.accounts, acc)
	// append to file
	var all []Account
	data, err := os.ReadFile(cfg.AccountsFile)
	if err == nil {
		json.Unmarshal(data, &all)
	}
	all = append(all, *acc)
	out, _ := json.MarshalIndent(all, "", "  ")
	os.WriteFile(cfg.AccountsFile, out, 0644)
	log.Printf("[pool] added account %s (total active: %d)", acc.Email, len(p.accounts))
}

func (p *AccountPool) Stats() map[string]interface{} {
	p.mu.RLock()
	defer p.mu.RUnlock()
	var totalReqs int64
	emails := []string{}
	for _, a := range p.accounts {
		totalReqs += atomic.LoadInt64(&a.TotalRequests)
		emails = append(emails, a.Email)
	}
	return map[string]interface{}{
		"active_accounts": len(p.accounts),
		"total_requests":  totalReqs,
		"emails":          emails,
	}
}

// --- Registration ---

var regClient = &http.Client{Timeout: 30 * time.Second}
var linkRegex = regexp.MustCompile(`https://[^\s"\x60]+`)

func regGetEmail() (string, error) {
	req, _ := http.NewRequest("GET", cfg.Registration.EmailAPIURL+"/api/generate-email", nil)
	req.Header.Set("x-api-key", cfg.Registration.EmailAPIKey)
	resp, err := regClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var result struct {
		Data struct {
			Email string `json:"email"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", err
	}
	return result.Data.Email, nil
}

func regGetLink(email string) (string, error) {
	for i := 0; i < 20; i++ {
		time.Sleep(2 * time.Second)
		req, _ := http.NewRequest("GET", cfg.Registration.EmailAPIURL+"/api/emails?email="+email, nil)
		req.Header.Set("x-api-key", cfg.Registration.EmailAPIKey)
		resp, err := regClient.Do(req)
		if err != nil {
			continue
		}
		var result struct {
			Data struct {
				Emails []struct {
					FromAddress string `json:"from_address"`
					Subject     string `json:"subject"`
					HTMLContent string `json:"html_content"`
				} `json:"emails"`
			} `json:"data"`
		}
		json.NewDecoder(resp.Body).Decode(&result)
		resp.Body.Close()
		for _, em := range result.Data.Emails {
			if strings.Contains(em.FromAddress, "emergent.sh") && strings.Contains(em.Subject, "Confirm") {
				m := linkRegex.FindString(em.HTMLContent)
				if m != "" {
					return m, nil
				}
			}
		}
	}
	return "", fmt.Errorf("confirmation link not found after 20 attempts")
}

func regInitAccount(jwt string) {
	body, _ := json.Marshal(map[string]interface{}{
		"ads_metadata": map[string]interface{}{"app_version": "1.1.28", "showError": ""},
	})
	req, _ := http.NewRequest("POST", cfg.Emergent.APIURL+"/user/details", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+jwt)
	regClient.Do(req)

	req2, _ := http.NewRequest("GET", cfg.Emergent.APIURL+"/credits/balance", nil)
	req2.Header.Set("Authorization", "Bearer "+jwt)
	regClient.Do(req2)
}

func registerOneAccount() (*Account, error) {
	rc := cfg.Registration
	authHeaders := map[string]string{
		"Apikey":        rc.SupabaseAPIKey,
		"Authorization": "Bearer " + rc.SupabaseAPIKey,
		"Origin":        cfg.Emergent.AppURL,
		"Referer":       cfg.Emergent.AppURL + "/",
	}

	// visit landing
	regClient.Get(cfg.Emergent.AppURL + "/landing/")

	// get email
	email, err := regGetEmail()
	if err != nil {
		return nil, fmt.Errorf("get email: %w", err)
	}
	log.Printf("[register] got email: %s", email)

	// signup
	signupBody, _ := json.Marshal(map[string]interface{}{
		"email": email, "password": email,
		"data":                  map[string]string{"name": "User"},
		"gotrue_meta_security":  map[string]interface{}{},
		"code_challenge":        nil,
		"code_challenge_method": nil,
	})
	req, _ := http.NewRequest("POST", rc.BaseAuthURL+"/auth/v1/signup", bytes.NewReader(signupBody))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range authHeaders {
		req.Header.Set(k, v)
	}
	resp, err := regClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("signup: %w", err)
	}
	resp.Body.Close()

	// get confirmation link
	link, err := regGetLink(email)
	if err != nil {
		return nil, fmt.Errorf("get link: %w", err)
	}
	log.Printf("[register] confirming: %s", email)
	regClient.Get(link)

	// get token
	tokenBody, _ := json.Marshal(map[string]interface{}{
		"email": email, "password": email,
		"gotrue_meta_security": map[string]interface{}{},
	})
	req2, _ := http.NewRequest("POST", rc.BaseAuthURL+"/auth/v1/token?grant_type=password", bytes.NewReader(tokenBody))
	req2.Header.Set("Content-Type", "application/json")
	for k, v := range authHeaders {
		req2.Header.Set(k, v)
	}
	resp2, err := regClient.Do(req2)
	if err != nil {
		return nil, fmt.Errorf("token: %w", err)
	}
	defer resp2.Body.Close()
	var tokenResp struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.NewDecoder(resp2.Body).Decode(&tokenResp); err != nil {
		return nil, fmt.Errorf("decode token: %w", err)
	}
	if tokenResp.AccessToken == "" {
		return nil, fmt.Errorf("empty access_token for %s", email)
	}

	regInitAccount(tokenResp.AccessToken)
	log.Printf("[register] success: %s", email)

	return &Account{
		JWT:       tokenResp.AccessToken,
		Email:     email,
		CreatedAt: float64(time.Now().Unix()),
		IsActive:  true,
	}, nil
}

func batchRegister(count int) []*Account {
	var results []*Account
	var mu sync.Mutex
	var wg sync.WaitGroup
	workers := 3
	if count < workers {
		workers = count
	}
	ch := make(chan int, count)
	for i := 0; i < count; i++ {
		ch <- i
	}
	close(ch)
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for id := range ch {
				acc, err := registerOneAccount()
				if err != nil {
					log.Printf("[register] #%d failed: %v", id, err)
					continue
				}
				mu.Lock()
				results = append(results, acc)
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	return results
}

var registering int32 // atomic flag to prevent concurrent replenish

func autoReplenish() {
	for {
		time.Sleep(30 * time.Second)
		active := pool.ActiveCount()
		if active < cfg.Registration.MinAccounts && atomic.CompareAndSwapInt32(&registering, 0, 1) {
			need := cfg.Registration.MaxAccounts - active
			if need <= 0 {
				need = cfg.Registration.MinAccounts
			}
			log.Printf("[replenish] active=%d < min=%d, registering %d accounts", active, cfg.Registration.MinAccounts, need)
			accs := batchRegister(need)
			for _, acc := range accs {
				pool.AddAccount(acc)
			}
			log.Printf("[replenish] done, added %d, total active=%d", len(accs), pool.ActiveCount())
			atomic.StoreInt32(&registering, 0)
		}
	}
}

type ContentBlock struct {
	Type         string                 `json:"type"`
	Text         string                 `json:"text,omitempty"`
	Thinking     string                 `json:"thinking,omitempty"`
	Signature    string                 `json:"signature,omitempty"`
	ID           string                 `json:"id,omitempty"`
	Name         string                 `json:"name,omitempty"`
	Input        json.RawMessage        `json:"input,omitempty"`
	ToolUseID    string                 `json:"tool_use_id,omitempty"`
	Content      interface{}            `json:"content,omitempty"`
	IsError      *bool                  `json:"is_error,omitempty"`
	CacheControl map[string]interface{} `json:"cache_control,omitempty"`
}

type AnthropicMessage struct {
	Role    string      `json:"role"`
	Content interface{} `json:"content"`
}

type ToolInputSchema struct {
	Type       string                 `json:"type"`
	Properties map[string]interface{} `json:"properties,omitempty"`
	Required   []string               `json:"required,omitempty"`
}

type Tool struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	InputSchema ToolInputSchema `json:"input_schema"`
}

type MessagesRequest struct {
	Model       string             `json:"model"`
	Messages    []AnthropicMessage `json:"messages"`
	Tools       []Tool             `json:"tools,omitempty"`
	MaxTokens   int                `json:"max_tokens,omitempty"`
	Stream      bool               `json:"stream,omitempty"`
	System      interface{}        `json:"system,omitempty"`
	Temperature *float64           `json:"temperature,omitempty"`
	ToolChoice  interface{}        `json:"tool_choice,omitempty"`
	Thinking    *ThinkingConfig    `json:"thinking,omitempty"`
}

type ThinkingConfig struct {
	Type         string `json:"type"`
	BudgetTokens int    `json:"budget_tokens,omitempty"`
}

type MessagesResponse struct {
	ID           string         `json:"id"`
	Type         string         `json:"type"`
	Role         string         `json:"role"`
	Content      []ContentBlock `json:"content"`
	Model        string         `json:"model"`
	StopReason   string         `json:"stop_reason"`
	StopSequence *string        `json:"stop_sequence"`
	Usage        Usage          `json:"usage"`
}

type Usage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

type OAIMessage struct {
	Role       string      `json:"role"`
	Content    interface{} `json:"content,omitempty"`
	ToolCalls  []ToolCall  `json:"tool_calls,omitempty"`
	ToolCallID string      `json:"tool_call_id,omitempty"`
	Name       string      `json:"name,omitempty"`
}

type ToolCall struct {
	ID       string       `json:"id"`
	Type     string       `json:"type"`
	Index    int          `json:"index"`
	Function FunctionCall `json:"function"`
}

type FunctionCall struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type OAITool struct {
	Type     string      `json:"type"`
	Function OAIFunction `json:"function"`
}

type OAIFunction struct {
	Name        string                 `json:"name"`
	Description string                 `json:"description,omitempty"`
	Parameters  map[string]interface{} `json:"parameters"`
}

type OAIRequest struct {
	Model       string       `json:"model"`
	Messages    []OAIMessage `json:"messages"`
	Tools       []OAITool    `json:"tools,omitempty"`
	MaxTokens   int          `json:"max_tokens,omitempty"`
	Stream      bool         `json:"stream,omitempty"`
	Temperature *float64     `json:"temperature,omitempty"`
	ToolChoice  interface{}  `json:"tool_choice,omitempty"`
}

type OAIChoice struct {
	Index        int        `json:"index"`
	Message      OAIMessage `json:"message"`
	FinishReason string     `json:"finish_reason"`
}

type OAIResponse struct {
	ID      string      `json:"id"`
	Model   string      `json:"model"`
	Choices []OAIChoice `json:"choices"`
	Usage   struct {
		PromptTokens     int `json:"prompt_tokens"`
		CompletionTokens int `json:"completion_tokens"`
	} `json:"usage"`
}

type OAIDelta struct {
	Role      string     `json:"role,omitempty"`
	Content   *string    `json:"content,omitempty"`
	ToolCalls []ToolCall `json:"tool_calls,omitempty"`
}

type OAIStreamChoice struct {
	Index        int      `json:"index"`
	Delta        OAIDelta `json:"delta"`
	FinishReason *string  `json:"finish_reason"`
}

type OAIStreamChunk struct {
	ID      string            `json:"id"`
	Model   string            `json:"model"`
	Choices []OAIStreamChoice `json:"choices"`
}

func contentToString(content interface{}) string {
	if content == nil {
		return ""
	}
	switch v := content.(type) {
	case string:
		return v
	case []interface{}:
		var parts []string
		for _, item := range v {
			if m, ok := item.(map[string]interface{}); ok {
				if t, _ := m["type"].(string); t == "text" {
					if s, _ := m["text"].(string); s != "" {
						parts = append(parts, s)
					}
				}
			}
		}
		return strings.Join(parts, "")
	}
	b, _ := json.Marshal(content)
	return string(b)
}

func systemToString(system interface{}) string {
	if system == nil {
		return ""
	}
	switch v := system.(type) {
	case string:
		return v
	case []interface{}:
		var parts []string
		for _, item := range v {
			if m, ok := item.(map[string]interface{}); ok {
				if tp, _ := m["type"].(string); tp == "text" {
					if s, _ := m["text"].(string); s != "" {
						parts = append(parts, s)
					}
				}
			}
		}
		return strings.Join(parts, "")
	}
	return ""
}

func anthropicToOAIMessages(msgs []AnthropicMessage, system interface{}) []OAIMessage {
	var result []OAIMessage
	if s := systemToString(system); s != "" {
		result = append(result, OAIMessage{Role: "system", Content: s})
	}
	for _, msg := range msgs {
		switch msg.Role {
		case "user":
			result = append(result, OAIMessage{Role: "user", Content: contentToString(msg.Content)})
		case "assistant":
			oaiMsg := OAIMessage{Role: "assistant"}
			var textParts []string
			var toolCalls []ToolCall
			switch c := msg.Content.(type) {
			case string:
				textParts = append(textParts, c)
			case []interface{}:
				for _, item := range c {
					m, ok := item.(map[string]interface{})
					if !ok {
						continue
					}
					switch m["type"] {
					case "text":
						if s, _ := m["text"].(string); s != "" {
							textParts = append(textParts, s)
						}
					case "tool_use":
						id, _ := m["id"].(string)
						name, _ := m["name"].(string)
						inputRaw, _ := json.Marshal(m["input"])
						toolCalls = append(toolCalls, ToolCall{
							ID: id, Type: "function",
							Function: FunctionCall{Name: name, Arguments: string(inputRaw)},
						})
					}
				}
			}
			if len(textParts) > 0 {
				oaiMsg.Content = strings.Join(textParts, "")
			}
			if len(toolCalls) > 0 {
				oaiMsg.ToolCalls = toolCalls
			}
			result = append(result, oaiMsg)
		case "tool":
			switch c := msg.Content.(type) {
			case []interface{}:
				for _, item := range c {
					m, ok := item.(map[string]interface{})
					if !ok {
						continue
					}
					if m["type"] == "tool_result" {
						tid, _ := m["tool_use_id"].(string)
						result = append(result, OAIMessage{
							Role: "tool", Content: contentToString(m["content"]), ToolCallID: tid,
						})
					}
				}
			}
		}
	}
	return result
}

func fixSchema(schema map[string]interface{}) map[string]interface{} {
	if schema == nil {
		return map[string]interface{}{"type": "object", "properties": map[string]interface{}{}}
	}
	if t, _ := schema["type"].(string); t == "object" {
		if _, ok := schema["properties"]; !ok {
			schema["properties"] = map[string]interface{}{}
		}
	}
	if props, ok := schema["properties"].(map[string]interface{}); ok {
		for k, v := range props {
			if sub, ok := v.(map[string]interface{}); ok {
				props[k] = fixSchema(sub)
			}
		}
	}
	if items, ok := schema["items"].(map[string]interface{}); ok {
		schema["items"] = fixSchema(items)
	}
	return schema
}

func anthropicToOAITools(tools []Tool) []OAITool {
	var result []OAITool
	for _, t := range tools {
		params := map[string]interface{}{
			"type": t.InputSchema.Type,
		}
		if t.InputSchema.Properties == nil {
			params["properties"] = map[string]interface{}{}
		} else {
			params["properties"] = t.InputSchema.Properties
		}
		if len(t.InputSchema.Required) > 0 {
			params["required"] = t.InputSchema.Required
		}
		params = fixSchema(params)
		result = append(result, OAITool{
			Type: "function",
			Function: OAIFunction{
				Name:        t.Name,
				Description: t.Description,
				Parameters:  params,
			},
		})
	}
	return result
}

func oaiResponseToAnthropic(oai OAIResponse, model string) MessagesResponse {
	var content []ContentBlock
	stopReason := "end_turn"
	if len(oai.Choices) > 0 {
		choice := oai.Choices[0]
		if s := choice.Message.Content; s != nil {
			if str, ok := s.(string); ok && str != "" {
				content = append(content, ContentBlock{Type: "text", Text: str})
			}
		}
		for _, tc := range choice.Message.ToolCalls {
			var inputRaw json.RawMessage
			inputRaw = json.RawMessage(tc.Function.Arguments)
			if !json.Valid(inputRaw) {
				inputRaw = json.RawMessage("{}")
			}
			content = append(content, ContentBlock{
				Type: "tool_use", ID: tc.ID, Name: tc.Function.Name, Input: inputRaw,
			})
		}
		if len(choice.Message.ToolCalls) > 0 {
			stopReason = "tool_use"
		} else {
			switch choice.FinishReason {
			case "length":
				stopReason = "max_tokens"
			case "tool_calls":
				stopReason = "tool_use"
			default:
				stopReason = "end_turn"
			}
		}
	}
	return MessagesResponse{
		ID:    oai.ID,
		Type:  "message",
		Role:  "assistant",
		Model: model,
		Content: content,
		StopReason: stopReason,
		Usage: Usage{
			InputTokens:  oai.Usage.PromptTokens,
			OutputTokens: oai.Usage.CompletionTokens,
		},
	}
}

var httpClient = &http.Client{Timeout: 120 * time.Second}

func callOojj(req MessagesRequest, w http.ResponseWriter) {
	oaiMsgs := anthropicToOAIMessages(req.Messages, req.System)
	oaiTools := anthropicToOAITools(req.Tools)
	oaiReq := OAIRequest{
		Model:       cfg.ToolUse.Model,
		Messages:    oaiMsgs,
		Tools:       oaiTools,
		MaxTokens:   req.MaxTokens,
		Stream:      req.Stream,
		Temperature: req.Temperature,
		ToolChoice:  req.ToolChoice,
	}
	body, _ := json.Marshal(oaiReq)
	httpReq, _ := http.NewRequest("POST", cfg.ToolUse.BaseURL+"/chat/completions", bytes.NewReader(body))
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+cfg.ToolUse.APIKey)
	if req.Stream {
		httpReq.Header.Set("Accept", "text/event-stream")
	}
	resp, err := httpClient.Do(httpReq)
	if err != nil {
		http.Error(w, "upstream error: "+err.Error(), 502)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		respBody, _ := io.ReadAll(resp.Body)
		log.Printf("[oojj] error %d: %s", resp.StatusCode, respBody)
		w.WriteHeader(resp.StatusCode)
		w.Write(respBody)
		return
	}
	if req.Stream {
		streamOojjToAnthropic(resp.Body, w, req.Model)
	} else {
		var oaiResp OAIResponse
		if err := json.NewDecoder(resp.Body).Decode(&oaiResp); err != nil {
			http.Error(w, "decode error: "+err.Error(), 502)
			return
		}
		anthResp := oaiResponseToAnthropic(oaiResp, req.Model)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(anthResp)
	}
}

func streamOojjToAnthropic(body io.Reader, w http.ResponseWriter, model string) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	flusher, _ := w.(http.Flusher)

	sendEvent := func(eventType string, data interface{}) {
		b, _ := json.Marshal(data)
		fmt.Fprintf(w, "event: %s\ndata: %s\n\n", eventType, b)
		if flusher != nil {
			flusher.Flush()
		}
	}

	msgID := fmt.Sprintf("msg_%d", time.Now().UnixNano())
	sendEvent("message_start", map[string]interface{}{
		"type": "message_start",
		"message": map[string]interface{}{
			"id": msgID, "type": "message", "role": "assistant",
			"model": model, "content": []interface{}{},
			"stop_reason": nil,
			"usage": map[string]int{"input_tokens": 0, "output_tokens": 0},
		},
	})

	textIdx := -1
	toolIndices := map[int]int{} // oai tool index -> anthropic block index
	blockCount := 0
	var stopReason string

	scanner := bufio.NewScanner(body)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		data := strings.TrimPrefix(line, "data: ")
		if data == "[DONE]" {
			break
		}
		var chunk OAIStreamChunk
		if err := json.Unmarshal([]byte(data), &chunk); err != nil {
			continue
		}
		if len(chunk.Choices) == 0 {
			continue
		}
		delta := chunk.Choices[0].Delta
		finish := chunk.Choices[0].FinishReason

		if delta.Content != nil && *delta.Content != "" {
			if textIdx == -1 {
				textIdx = blockCount
				blockCount++
				sendEvent("content_block_start", map[string]interface{}{
					"type": "content_block_start", "index": textIdx,
					"content_block": map[string]interface{}{"type": "text", "text": ""},
				})
			}
			sendEvent("content_block_delta", map[string]interface{}{
				"type": "content_block_delta", "index": textIdx,
				"delta": map[string]interface{}{"type": "text_delta", "text": *delta.Content},
			})
		}

		for _, tc := range delta.ToolCalls {
			anthIdx, exists := toolIndices[tc.Index]
			if !exists {
				anthIdx = blockCount
				blockCount++
				toolIndices[tc.Index] = anthIdx
				sendEvent("content_block_start", map[string]interface{}{
					"type": "content_block_start", "index": anthIdx,
					"content_block": map[string]interface{}{
						"type": "tool_use", "id": tc.ID,
						"name": tc.Function.Name, "input": map[string]interface{}{},
					},
				})
			}
			if tc.Function.Arguments != "" {
				sendEvent("content_block_delta", map[string]interface{}{
					"type": "content_block_delta", "index": anthIdx,
					"delta": map[string]interface{}{"type": "input_json_delta", "partial_json": tc.Function.Arguments},
				})
			}
			stopReason = "tool_use"
		}

		if finish != nil {
			if stopReason == "" {
				switch *finish {
				case "length":
					stopReason = "max_tokens"
				case "tool_calls":
					stopReason = "tool_use"
				default:
					stopReason = "end_turn"
				}
			}
		}
	}

	for i := 0; i < blockCount; i++ {
		sendEvent("content_block_stop", map[string]interface{}{"type": "content_block_stop", "index": i})
	}
	if stopReason == "" {
		stopReason = "end_turn"
	}
	sendEvent("message_delta", map[string]interface{}{
		"type": "message_delta",
		"delta": map[string]interface{}{"stop_reason": stopReason, "stop_sequence": nil},
		"usage": map[string]int{"output_tokens": 0},
	})
	sendEvent("message_stop", map[string]interface{}{"type": "message_stop"})
}

type EmergentJobResp struct {
	ID string `json:"id"`
	ClientRefID string `json:"client_ref_id"`
}

type TrajEvent struct {
	Type    string          `json:"type"`
	Payload json.RawMessage `json:"payload"`
}

type TrajPayload struct {
	Thought string `json:"thought"`
	Output  string `json:"output"`
	Status  string `json:"status"`
}

type TrajHistory struct {
	History []TrajEvent `json:"history"`
	Status  string      `json:"status"`
}

func buildEmergentPrompt(req MessagesRequest) string {
	var parts []string
	if s := systemToString(req.System); s != "" {
		parts = append(parts, "<system>\n"+s+"\n</system>")
	}
	for _, msg := range req.Messages {
		switch msg.Role {
		case "user":
			parts = append(parts, "Human: "+contentToString(msg.Content))
		case "assistant":
			parts = append(parts, "Assistant: "+contentToString(msg.Content))
		}
	}
	parts = append(parts, "Assistant:")
	return strings.Join(parts, "\n\n")
}

func callEmergent(req MessagesRequest, acc *Account, w http.ResponseWriter) {
	text, thinking := callEmergentRaw(buildEmergentPrompt(req), acc, w)
	if text == "" && thinking == "" {
		return // error already written
	}
	if req.Stream {
		emergentStream(text, thinking, req.Model, w)
	} else {
		msgID := fmt.Sprintf("msg_%d", time.Now().UnixNano())
		var content []ContentBlock
		if thinking != "" {
			content = append(content, ContentBlock{Type: "thinking", Thinking: thinking})
		}
		content = append(content, ContentBlock{Type: "text", Text: text})
		anthResp := MessagesResponse{
			ID: msgID, Type: "message", Role: "assistant", Model: req.Model,
			Content:    content,
			StopReason: "end_turn",
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(anthResp)
	}
}

// callEmergentRaw submits to emergent and polls for result. Returns (text, thinking).
// On error, writes HTTP error to w and returns empty strings.
func callEmergentRaw(prompt string, acc *Account, w http.ResponseWriter) (string, string) {
	refID := fmt.Sprintf("%d", time.Now().UnixNano())
	jobBody := map[string]interface{}{
		"client_ref_id": refID,
		"payload": map[string]interface{}{
			"processor_type":          "env_only",
			"is_cloud":                true,
			"env_image":               "us-central1-docker.pkg.dev/emergent-default/emergent-container-hub/fastapi_react_mongo_shadcn_base_image_cloud_arm:release-26092025-2",
			"branch":                  "",
			"repository":              "",
			"enable_visual_edit":      true,
			"prompt_name":             "auto_prompt_selector",
			"prompt_version":          "latest",
			"work_space_dir":          "",
			"task":                    prompt,
			"model_name":              "claude-opus-4-5",
			"model_manually_selected": true,
			"per_instance_cost_limit": 25,
			"agentic_skills":          []interface{}{},
			"plugin_version":          "release-10092025-1",
			"base64_image_list":       []interface{}{},
			"human_timestamp":         time.Now().UnixMilli(),
			"asset_upload_enabled":    true,
			"is_pro_user":             false,
			"testMode":                false,
			"thinking_level":          "thinking",
			"job_mode":                "public",
			"mcp_id":                  []interface{}{},
		},
		"model_name": "claude-opus-4-5",
		"resume":     false,
		"ads_metadata": map[string]interface{}{
			"app_version": "1.1.28",
		},
	}
	body, _ := json.Marshal(jobBody)
	httpReq, _ := http.NewRequest("POST", cfg.Emergent.APIURL+"/jobs/v0/submit-queue/", bytes.NewReader(body))
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+acc.JWT)
	resp, err := httpClient.Do(httpReq)
	if err != nil {
		http.Error(w, "emergent submit error: "+err.Error(), 502)
		return "", ""
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 && resp.StatusCode != 201 {
		respBody, _ := io.ReadAll(resp.Body)
		log.Printf("[emergent] submit error %d: %s", resp.StatusCode, respBody)
		w.WriteHeader(resp.StatusCode)
		w.Write(respBody)
		return "", ""
	}
	convID := refID
	// try to get ID from response, fall back to refID
	var jobResp EmergentJobResp
	if err := json.NewDecoder(resp.Body).Decode(&jobResp); err == nil {
		if jobResp.ID != "" {
			convID = jobResp.ID
		} else if jobResp.ClientRefID != "" {
			convID = jobResp.ClientRefID
		}
	}
	log.Printf("[emergent] job submitted, conv_id=%s", convID)

	var text, thinking string
	done := false
	for i := 0; i < cfg.MaxPollAttempts; i++ {
		time.Sleep(time.Duration(cfg.PollIntervalSeconds) * time.Second)

		// Try trajectories endpoint (Python-style)
		trajReq, _ := http.NewRequest("GET", cfg.Emergent.APIURL+"/trajectories/v0/"+convID+"/history?limit=50", nil)
		trajReq.Header.Set("Authorization", "Bearer "+acc.JWT)
		trajResp, err := httpClient.Do(trajReq)
		if err != nil {
			log.Printf("[emergent] poll error: %v", err)
			continue
		}
		var trajData struct {
			LatestRequestID *string `json:"latest_request_id"`
			Data            []struct {
				TrajPayload struct {
					ReasoningContent string `json:"reasoning_content"`
					FunctionName     string `json:"function_name"`
					Action           string `json:"action"`
					Thought          string `json:"thought"`
					Observation      string `json:"observation"`
				} `json:"traj_payload"`
			} `json:"data"`
			// fallback fields for old endpoint format
			History []TrajEvent `json:"history"`
			Status  string      `json:"status"`
		}
		json.NewDecoder(trajResp.Body).Decode(&trajData)
		trajResp.Body.Close()

		// Parse new-style trajectories response
		if len(trajData.Data) > 0 {
			for _, item := range trajData.Data {
				p := item.TrajPayload
				if p.ReasoningContent != "" {
					thinking = p.ReasoningContent
				}
				var t string
				if p.FunctionName == "ask_human" {
					t = p.Action
				} else if p.FunctionName != "" {
					t = p.Action
					if t == "" {
						t = p.Thought
					}
				} else {
					t = p.Thought
					if t == "" {
						t = p.Observation
					}
				}
				if t != "" {
					text = t
				}
			}
			if trajData.LatestRequestID != nil {
				done = true
			}
		}

		// Parse old-style history response as fallback
		for _, ev := range trajData.History {
			switch ev.Type {
			case "default_tool", "agent_action":
				var p TrajPayload
				if err := json.Unmarshal(ev.Payload, &p); err == nil {
					if p.Output != "" {
						text = p.Output
					}
					if p.Thought != "" {
						thinking = p.Thought
					}
				}
			case "job_complete", "final_response":
				var p TrajPayload
				if err := json.Unmarshal(ev.Payload, &p); err == nil {
					if p.Output != "" {
						text = p.Output
					}
				}
				done = true
			}
		}
		if trajData.Status == "completed" || trajData.Status == "done" {
			done = true
		}
		if done && text != "" {
			break
		}
	}
	log.Printf("[emergent] conv=%s done=%v text_len=%d", convID, done, len(text))
	return text, thinking
}

func emergentStream(text, thinking, model string, w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	flusher, _ := w.(http.Flusher)

	sendEvent := func(eventType string, data interface{}) {
		b, _ := json.Marshal(data)
		fmt.Fprintf(w, "event: %s\ndata: %s\n\n", eventType, b)
		if flusher != nil {
			flusher.Flush()
		}
	}

	msgID := fmt.Sprintf("msg_%d", time.Now().UnixNano())
	sendEvent("message_start", map[string]interface{}{
		"type": "message_start",
		"message": map[string]interface{}{
			"id": msgID, "type": "message", "role": "assistant",
			"model": model, "content": []interface{}{},
			"stop_reason": nil,
			"usage": map[string]int{"input_tokens": 0, "output_tokens": 0},
		},
	})

	blockIdx := 0
	chunkSize := 20

	// 如果有 thinking 内容，先输出 thinking 块
	if thinking != "" {
		sendEvent("content_block_start", map[string]interface{}{
			"type": "content_block_start", "index": blockIdx,
			"content_block": map[string]interface{}{"type": "thinking", "thinking": ""},
		})
		runes := []rune(thinking)
		for i := 0; i < len(runes); i += chunkSize {
			end := i + chunkSize
			if end > len(runes) {
				end = len(runes)
			}
			sendEvent("content_block_delta", map[string]interface{}{
				"type": "content_block_delta", "index": blockIdx,
				"delta": map[string]interface{}{"type": "thinking_delta", "thinking": string(runes[i:end])},
			})
		}
		sendEvent("content_block_stop", map[string]interface{}{"type": "content_block_stop", "index": blockIdx})
		blockIdx++
	}

	// 输出 text 块
	sendEvent("content_block_start", map[string]interface{}{
		"type": "content_block_start", "index": blockIdx,
		"content_block": map[string]interface{}{"type": "text", "text": ""},
	})
	runes := []rune(text)
	for i := 0; i < len(runes); i += chunkSize {
		end := i + chunkSize
		if end > len(runes) {
			end = len(runes)
		}
		sendEvent("content_block_delta", map[string]interface{}{
			"type": "content_block_delta", "index": blockIdx,
			"delta": map[string]interface{}{"type": "text_delta", "text": string(runes[i:end])},
		})
	}
	sendEvent("content_block_stop", map[string]interface{}{"type": "content_block_stop", "index": blockIdx})
	sendEvent("message_delta", map[string]interface{}{
		"type": "message_delta",
		"delta": map[string]interface{}{"stop_reason": "end_turn", "stop_sequence": nil},
		"usage": map[string]int{"output_tokens": len(runes)},
	})
	sendEvent("message_stop", map[string]interface{}{"type": "message_stop"})
}

var pool *AccountPool

func hasTools(req MessagesRequest) bool {
	return len(req.Tools) > 0
}

func anthropicError(w http.ResponseWriter, status int, errType, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"type": "error",
		"error": map[string]string{
			"type":    errType,
			"message": msg,
		},
	})
}

func setRateLimitHeaders(w http.ResponseWriter) {
	w.Header().Set("anthropic-ratelimit-requests-limit", "1000")
	w.Header().Set("anthropic-ratelimit-requests-remaining", "999")
	w.Header().Set("anthropic-ratelimit-tokens-limit", "100000")
	w.Header().Set("anthropic-ratelimit-tokens-remaining", "99000")
}

func messagesHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		anthropicError(w, 405, "invalid_request_error", "method not allowed")
		return
	}
	setRateLimitHeaders(w)
	var req MessagesRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		anthropicError(w, 400, "invalid_request_error", "bad request: "+err.Error())
		return
	}
	log.Printf("[handler] model=%s stream=%v tools=%d msgs=%d",
		req.Model, req.Stream, len(req.Tools), len(req.Messages))

	if hasTools(req) {
		log.Printf("[handler] routing to oojj (has tools)")
		callOojj(req, w)
	} else {
		acc := pool.Get()
		if acc == nil {
			log.Printf("[handler] no active accounts, attempting on-demand registration")
			newAcc, err := registerOneAccount()
			if err != nil {
				anthropicError(w, 503, "api_error", "no active accounts and registration failed: "+err.Error())
				return
			}
			pool.AddAccount(newAcc)
			acc = newAcc
		}
		log.Printf("[handler] routing to emergent (no tools), account=%s", acc.Email)
		callEmergent(req, acc, w)
	}
}

func modelsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"data": []map[string]interface{}{
			{"id": "claude-opus-4-5", "object": "model", "created": 0, "owned_by": "anthropic"},
			{"id": "claude-sonnet-4-5", "object": "model", "created": 0, "owned_by": "anthropic"},
			{"id": "claude-3-5-sonnet-20241022", "object": "model", "created": 0, "owned_by": "anthropic"},
		},
	})
}

// --- OpenAI /v1/chat/completions endpoint ---

type OAIChatRequest struct {
	Model       string       `json:"model"`
	Messages    []OAIMessage `json:"messages"`
	Tools       []OAITool    `json:"tools,omitempty"`
	MaxTokens   int          `json:"max_tokens,omitempty"`
	Stream      bool         `json:"stream,omitempty"`
	Temperature *float64     `json:"temperature,omitempty"`
	ToolChoice  interface{}  `json:"tool_choice,omitempty"`
}

func oaiToAnthropicMessages(msgs []OAIMessage) ([]AnthropicMessage, string) {
	var system string
	var result []AnthropicMessage
	for _, m := range msgs {
		switch m.Role {
		case "system":
			system = contentToString(m.Content)
		case "user":
			result = append(result, AnthropicMessage{Role: "user", Content: contentToString(m.Content)})
		case "assistant":
			if len(m.ToolCalls) > 0 {
				var blocks []interface{}
				if s := contentToString(m.Content); s != "" {
					blocks = append(blocks, map[string]interface{}{"type": "text", "text": s})
				}
				for _, tc := range m.ToolCalls {
					var input interface{}
					json.Unmarshal([]byte(tc.Function.Arguments), &input)
					blocks = append(blocks, map[string]interface{}{
						"type": "tool_use", "id": tc.ID, "name": tc.Function.Name, "input": input,
					})
				}
				result = append(result, AnthropicMessage{Role: "assistant", Content: blocks})
			} else {
				result = append(result, AnthropicMessage{Role: "assistant", Content: contentToString(m.Content)})
			}
		case "tool":
			result = append(result, AnthropicMessage{
				Role: "user",
				Content: []interface{}{
					map[string]interface{}{
						"type":        "tool_result",
						"tool_use_id": m.ToolCallID,
						"content":     contentToString(m.Content),
					},
				},
			})
		}
	}
	return result, system
}

func oaiToolsToAnthropic(tools []OAITool) []Tool {
	var result []Tool
	for _, t := range tools {
		props, _ := t.Function.Parameters["properties"].(map[string]interface{})
		reqd, _ := t.Function.Parameters["required"].([]interface{})
		var required []string
		for _, r := range reqd {
			if s, ok := r.(string); ok {
				required = append(required, s)
			}
		}
		result = append(result, Tool{
			Name:        t.Function.Name,
			Description: t.Function.Description,
			InputSchema: ToolInputSchema{
				Type:       "object",
				Properties: props,
				Required:   required,
			},
		})
	}
	return result
}

func anthropicToOAIResponse(anthResp MessagesResponse) map[string]interface{} {
	var content string
	var toolCalls []map[string]interface{}
	for _, block := range anthResp.Content {
		switch block.Type {
		case "text":
			content += block.Text
		case "tool_use":
			args, _ := json.Marshal(block.Input)
			toolCalls = append(toolCalls, map[string]interface{}{
				"id": block.ID, "type": "function",
				"function": map[string]string{"name": block.Name, "arguments": string(args)},
			})
		}
	}
	finishReason := "stop"
	if anthResp.StopReason == "tool_use" {
		finishReason = "tool_calls"
	} else if anthResp.StopReason == "max_tokens" {
		finishReason = "length"
	}
	msg := map[string]interface{}{"role": "assistant"}
	if content != "" {
		msg["content"] = content
	}
	if len(toolCalls) > 0 {
		msg["tool_calls"] = toolCalls
	}
	return map[string]interface{}{
		"id":      anthResp.ID,
		"object":  "chat.completion",
		"model":   anthResp.Model,
		"choices": []map[string]interface{}{{"index": 0, "message": msg, "finish_reason": finishReason}},
		"usage": map[string]int{
			"prompt_tokens": anthResp.Usage.InputTokens, "completion_tokens": anthResp.Usage.OutputTokens,
			"total_tokens": anthResp.Usage.InputTokens + anthResp.Usage.OutputTokens,
		},
	}
}

func chatCompletionsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":{"message":"method not allowed"}}`, 405)
		return
	}
	var oaiReq OAIChatRequest
	if err := json.NewDecoder(r.Body).Decode(&oaiReq); err != nil {
		http.Error(w, `{"error":{"message":"bad request"}}`, 400)
		return
	}
	log.Printf("[oai-compat] model=%s stream=%v tools=%d msgs=%d",
		oaiReq.Model, oaiReq.Stream, len(oaiReq.Tools), len(oaiReq.Messages))

	anthMsgs, system := oaiToAnthropicMessages(oaiReq.Messages)
	maxTokens := oaiReq.MaxTokens
	if maxTokens == 0 {
		maxTokens = 16384
	}
	anthReq := MessagesRequest{
		Model:       oaiReq.Model,
		Messages:    anthMsgs,
		MaxTokens:   maxTokens,
		Stream:      false, // we handle streaming separately below
		Temperature: oaiReq.Temperature,
	}
	if system != "" {
		anthReq.System = system
	}
	if len(oaiReq.Tools) > 0 {
		anthReq.Tools = oaiToolsToAnthropic(oaiReq.Tools)
	}

	if len(anthReq.Tools) > 0 {
		// route to oojj for tool use — forward as OAI directly
		oaiForward := OAIRequest{
			Model:       cfg.ToolUse.Model,
			Messages:    oaiReq.Messages,
			Tools:       oaiReq.Tools,
			MaxTokens:   maxTokens,
			Stream:      oaiReq.Stream,
			Temperature: oaiReq.Temperature,
			ToolChoice:  oaiReq.ToolChoice,
		}
		body, _ := json.Marshal(oaiForward)
		httpReq, _ := http.NewRequest("POST", cfg.ToolUse.BaseURL+"/chat/completions", bytes.NewReader(body))
		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Authorization", "Bearer "+cfg.ToolUse.APIKey)
		if oaiReq.Stream {
			httpReq.Header.Set("Accept", "text/event-stream")
		}
		resp, err := httpClient.Do(httpReq)
		if err != nil {
			http.Error(w, `{"error":{"message":"upstream error"}}`, 502)
			return
		}
		defer resp.Body.Close()
		// pass through
		for k, vs := range resp.Header {
			for _, v := range vs {
				w.Header().Add(k, v)
			}
		}
		w.WriteHeader(resp.StatusCode)
		io.Copy(w, resp.Body)
		return
	}

	// no tools — route to emergent
	acc := pool.Get()
	if acc == nil {
		newAcc, err := registerOneAccount()
		if err != nil {
			http.Error(w, `{"error":{"message":"no accounts available"}}`, 503)
			return
		}
		pool.AddAccount(newAcc)
		acc = newAcc
	}

	// For emergent, we need to get the response then convert to OAI format
	// Build prompt and call emergent inline
	prompt := buildEmergentPrompt(anthReq)
	text, thinking := callEmergentRaw(prompt, acc, w)
	if text == "" && thinking == "" {
		return // error already written by callEmergentRaw
	}

	if oaiReq.Stream {
		streamOAIResponse(text, oaiReq.Model, w)
	} else {
		msgID := fmt.Sprintf("chatcmpl-%d", time.Now().UnixNano())
		var content []ContentBlock
		if thinking != "" {
			content = append(content, ContentBlock{Type: "thinking", Thinking: thinking})
		}
		content = append(content, ContentBlock{Type: "text", Text: text})
		anthResp := MessagesResponse{
			ID: msgID, Type: "message", Role: "assistant", Model: oaiReq.Model,
			Content: content, StopReason: "end_turn",
		}
		oaiOut := anthropicToOAIResponse(anthResp)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(oaiOut)
	}
}

func streamOAIResponse(text, model string, w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	flusher, _ := w.(http.Flusher)
	id := fmt.Sprintf("chatcmpl-%d", time.Now().UnixNano())

	runes := []rune(text)
	chunkSize := 20
	for i := 0; i < len(runes); i += chunkSize {
		end := i + chunkSize
		if end > len(runes) {
			end = len(runes)
		}
		chunk := map[string]interface{}{
			"id": id, "object": "chat.completion.chunk", "model": model,
			"choices": []map[string]interface{}{
				{"index": 0, "delta": map[string]string{"content": string(runes[i:end])}, "finish_reason": nil},
			},
		}
		b, _ := json.Marshal(chunk)
		fmt.Fprintf(w, "data: %s\n\n", b)
		if flusher != nil {
			flusher.Flush()
		}
	}
	// final chunk
	final := map[string]interface{}{
		"id": id, "object": "chat.completion.chunk", "model": model,
		"choices": []map[string]interface{}{
			{"index": 0, "delta": map[string]interface{}{}, "finish_reason": "stop"},
		},
	}
	b, _ := json.Marshal(final)
	fmt.Fprintf(w, "data: %s\n\n", b)
	fmt.Fprintf(w, "data: [DONE]\n\n")
	if flusher != nil {
		flusher.Flush()
	}
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":          "ok",
		"active_accounts": pool.ActiveCount(),
		"service":         "hybrid-proxy-go",
	})
}

func statsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(pool.Stats())
}

func adminRegisterHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", 405)
		return
	}
	count := 3
	var body struct {
		Count int `json:"count"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err == nil && body.Count > 0 {
		count = body.Count
	}
	log.Printf("[admin] registering %d accounts", count)
	go func() {
		accs := batchRegister(count)
		for _, acc := range accs {
			pool.AddAccount(acc)
		}
		log.Printf("[admin] registered %d accounts, total active=%d", len(accs), pool.ActiveCount())
	}()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"message": fmt.Sprintf("registering %d accounts in background", count),
	})
}

func main() {
	cfg = loadSettings()
	pool = NewAccountPool(cfg.AccountsFile)
	if pool.ActiveCount() == 0 {
		log.Println("[warn] no active accounts loaded — emergent routing will fail until registration")
	}

	// start auto-replenish
	go autoReplenish()

	mux := http.NewServeMux()
	mux.HandleFunc("/v1/messages", messagesHandler)
	mux.HandleFunc("/v1/chat/completions", chatCompletionsHandler)
	mux.HandleFunc("/v1/models", modelsHandler)
	mux.HandleFunc("/health", healthHandler)
	mux.HandleFunc("/stats", statsHandler)
	mux.HandleFunc("/admin/register", adminRegisterHandler)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"status":"ok","service":"hybrid-proxy"}`))
	})

	log.Printf("[server] listening on %s (active accounts: %d)", cfg.ListenAddr, pool.ActiveCount())
	if err := http.ListenAndServe(cfg.ListenAddr, mux); err != nil {
		log.Fatal(err)
	}
}
