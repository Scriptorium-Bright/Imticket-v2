package org.example.ticket.venue.domain;

import jakarta.persistence.*;
import lombok.*;

@Entity
@Table(name = "venue_hall_seat_template",
        uniqueConstraints = @UniqueConstraint(name = "uk_hall_seat_position",
                columnNames = {"venue_hall_id", "seat_section", "seat_row", "seat_number"}))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class VenueHallSeatTemplate {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "venue_hall_id", nullable = false)
    private VenueHall venueHall;
    @Column(name = "seat_section", nullable = false, length = 20)
    private String section;
    @Column(name = "seat_row", nullable = false)
    private Integer rowNumber;
    @Column(name = "seat_number", nullable = false)
    private Integer seatNumber;
    @Enumerated(EnumType.STRING)
    @Column(name = "seat_grade", nullable = false, length = 10)
    private SeatGrade grade;
}
