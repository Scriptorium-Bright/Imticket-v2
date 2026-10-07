package org.example.ticket.payment.dto;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
@JsonIgnoreProperties(ignoreUnknown = true)
public record PortOneAmount(Integer total) {}
