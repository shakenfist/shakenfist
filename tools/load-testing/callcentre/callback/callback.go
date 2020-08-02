package callback

import (
	"encoding/json"
	"fmt"
	"net/http"
)

type CallBack struct {
	Received chan int
}

func NewCallBack() *CallBack {
	return &CallBack{
		Received: make(chan int),
	}
}

func (c *CallBack) StartServer() {
	// Start HTTP server
	go func() {
		http.HandleFunc("/phone", c.phone)

		if err := http.ListenAndServe(":8089", nil); err != nil {
			panic("Cannot start HTTP server:" + err.Error())
		}
	}()
}

type CallbackMsg struct {
	MachineIndex int `json:"machine_id"`
}

func (c *CallBack) phone(w http.ResponseWriter, req *http.Request) {
	var p CallbackMsg

	err := json.NewDecoder(req.Body).Decode(&p)
	if err != nil {
		fmt.Println("Received bad data:", err)
		return
	}

	c.Received <- p.MachineIndex
}
