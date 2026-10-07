package org.example.ticket.seat.dto;
import org.example.ticket.seat.domain.SeatStatus;
import org.example.ticket.venue.domain.SeatGrade;
public record SeatResponse(Long id, String section, Integer rowNumber, Integer seatNumber,
                           SeatGrade grade, Integer price, SeatStatus status) {}
